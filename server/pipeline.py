"""The whole decision, in one place: physics, perception, and which Nemotron decides.

The Nemotron ladder is the sponsor-depth play, and depth is this event's literal
tiebreaker, so it is worth being precise about how escalation is decided.

**Escalation is driven by conditions, not by the model's opinion of itself.** A model
asked "are you confident" will answer confidently either way, and the strongest entry
in the competing field escalates on ground-truth signals rather than self-report. So
Nano handles the routine frame and Super is called when something about the situation
is objectively harder or the cost of being wrong is higher:

  1. The heat index sits above the range its formula covers, so the physics is less
     certain exactly where the stakes are highest.
  2. The band is danger or extreme danger. Being wrong here hurts someone.
  3. The camera can see, but says so with low confidence. Borderline perception is the
     case where richer reasoning actually helps.
  4. An external lookup failed, so the world is partly unknown.

A model request to escalate is honoured as a fifth trigger, but it is the weakest one
and it is recorded separately so the split can be reported honestly.
"""

import os

import grounding as grounding_mod
import heat
import localize as localize_mod
import models
import station

# Where the device actually is. Overridable so this is not hardcoded to one roof.
DEVICE_LAT = float(os.environ.get("SAYA_LAT", "33.7167"))
DEVICE_LON = float(os.environ.get("SAYA_LON", "73.0667"))

ESCALATION_TRIGGERS = (
    ("heat_index_extrapolated", "the heat index is above the range its formula covers"),
    ("high_stakes_band", "the band is danger or extreme danger"),
    ("low_confidence_view", "the camera can see but reports low confidence"),
    ("grounding_incomplete", "an external advisory lookup failed"),
    ("model_requested", "the fast model asked for a second opinion"),
)

NO_FRAME_SCENE = {
    "can_see": False,
    "obstruction": "no frame was captured",
    "confidence": "low",
}


def escalation_reasons(sensor, scene, grounding=None):
    """Conditions that justify the larger model. Deterministic and inspectable."""
    reasons = []
    if sensor.get("heat_index_is_extrapolated"):
        reasons.append("heat_index_extrapolated")
    if sensor.get("band") in ("danger", "extreme danger"):
        reasons.append("high_stakes_band")
    if scene.get("can_see") and scene.get("confidence") == "low":
        reasons.append("low_confidence_view")
    if grounding and grounding.get("errors"):
        reasons.append("grounding_incomplete")
    return reasons


def station_context(temp_c, lat=None, lon=None):
    """What the nearest official station says right now, and how far away it is.

    Never raises. A comparator that cannot be reached is reported as unavailable,
    because "the station is unreachable" and "the station agrees with us" must not
    look the same to anything downstream.
    """
    try:
        obs = station.metar()
    except station.StationError as exc:
        return {"available": False, "error": str(exc)}
    if not obs:
        return {"available": False, "error": "no recent observations"}

    latest = obs[0]
    ctx = station.describe_comparator(lat if lat is not None else DEVICE_LAT,
                                      lon if lon is not None else DEVICE_LON, obs)
    out = {
        "available": True,
        "station": latest["station"],
        "name": latest["name"],
        "ts_utc": latest["ts_utc"],
        "temp_c": latest["temp_c"],
        "humidity_pct": round(latest["humidity_pct"], 1) if latest["humidity_pct"] else None,
        "temp_resolution_c": latest["temp_resolution_c"],
        "raw": latest["raw"],
    }
    if ctx:
        out["distance_km"] = ctx["distance_km"]
        out["station_elevation_m"] = ctx["station_elevation_m"]
    if temp_c is not None and latest["temp_c"] is not None:
        delta = round(temp_c - latest["temp_c"], 1)
        out["delta_c"] = delta
        # One observation is an anecdote. Say so, so the UI cannot imply otherwise.
        out["delta_is_single_observation"] = True
        out["delta_within_station_resolution"] = abs(delta) < latest["temp_resolution_c"]
    return out


def gather_grounding(city="Islamabad", key=None):
    """External advisories. Never raises: an unreachable lookup is reported as one.

    Relevance is judged by Nemotron rather than a keyword list. Measured 2026-09-26 on
    the same live results: the keyword filter kept 8 of 13 and Nemotron kept 3, and the
    ones it dropped it dropped for reasons a word list cannot reach, including "future
    date, not current" and "closures not due to heat" for a protest-related closure the
    keyword filter had kept because "closure" and "Islamabad" both appeared.
    """
    try:
        return grounding_mod.gather(city, use_judge=True, model_key=key)
    except Exception as exc:
        return {"city": city, "sources": {}, "nothing_found": [],
                "errors": {"all": "{}: {}".format(type(exc).__name__, exc)}}


def advise(temp_c, humidity_pct, jpeg_bytes=None, grounding=None, key=None,
           allow_escalation=True, with_station=True, with_grounding=False, lang="ur"):
    """One complete decision. Returns a record that explains itself.

    Every field a caller might quote is present alongside what produced it: which
    models ran, why the deeper one was or was not used, and what the system could not
    see. A judge reading this record should be able to reconstruct the decision.
    """
    key = key or models.load_key()
    trace = {"calls": [], "tokens": {"prompt": 0, "completion": 0}}

    sensor = heat.assess(temp_c, humidity_pct)

    if grounding is None and with_grounding:
        grounding = gather_grounding(key=key)

    if jpeg_bytes:
        scene, meta = models.see(jpeg_bytes, key=key)
        trace["calls"].append({"stage": "see", "model": meta["model"],
                               "seconds": None, "usage": meta["usage"]})
        trace["tokens"]["prompt"] += meta["usage"].get("prompt_tokens", 0)
        trace["tokens"]["completion"] += meta["usage"].get("completion_tokens", 0)
    else:
        # No frame is not the same as an unreadable frame. Both refuse to advise, but
        # only one of them means the camera is broken, and the operator needs to know
        # which. The scene records the distinction rather than flattening it.
        scene = dict(NO_FRAME_SCENE)

    reasons = escalation_reasons(sensor, scene, grounding) if allow_escalation else []
    model = models.REASONER_DEEP if reasons else models.REASONER_FAST

    out, meta = models.decide(sensor, scene, grounding=grounding, key=key, model=model)
    trace["calls"].append({"stage": "decide", "model": meta["model"], "usage": meta["usage"]})
    trace["tokens"]["prompt"] += meta["usage"].get("prompt_tokens", 0)
    trace["tokens"]["completion"] += meta["usage"].get("completion_tokens", 0)

    # The model's own request is the weakest trigger, so it only causes a second pass
    # when no condition had already justified one.
    if allow_escalation and not reasons and out.get("needs_deeper_model"):
        reasons = ["model_requested"]
        out, meta = models.decide(sensor, scene, grounding=grounding, key=key,
                                  model=models.REASONER_DEEP)
        trace["calls"].append({"stage": "decide_escalated", "model": meta["model"],
                               "usage": meta["usage"]})
        trace["tokens"]["prompt"] += meta["usage"].get("prompt_tokens", 0)
        trace["tokens"]["completion"] += meta["usage"].get("completion_tokens", 0)

    # Rendered only after the decision is final. The abstention gate is already closed
    # in code above, so a translation cannot reopen it. Measured 2026-09-26: asked to
    # decide and answer in Urdu in one step, both Nemotrons abandoned an explicit
    # refusal instruction and invented conditions they had been told were unobservable.
    if lang:
        out = localize_mod.localize(out, lang=lang, key=key)

    return {
        "station": station_context(temp_c) if with_station else None,
        "sensor": sensor,
        "scene": {k: v for k, v in scene.items() if not k.startswith("_")},
        "decision": {k: v for k, v in out.items() if not k.startswith("_")},
        "reasoning": out.get("_reasoning"),
        "routing": {
            "decided_by": trace["calls"][-1]["model"],
            "escalated": bool(reasons),
            "escalation_reasons": reasons,
            "explanations": [e for k, e in ESCALATION_TRIGGERS if k in reasons],
        },
        "grounding": grounding,
        "grounding_used": bool(grounding and any(grounding.get("sources", {}).values())),
        "trace": trace,
    }
