#!/usr/bin/env python3
"""Escalation policy tests. Offline, no key, no API calls.

Escalation is the Nemotron ladder, and depth of sponsor use is this event's literal
tiebreaker, so the policy has to be inspectable rather than vibes. It is driven by
conditions about the situation, not by asking the model how sure it feels.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import pipeline  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILURES.append(name)


def sensor(band="caution", extrapolated=False):
    return {"band": band, "heat_index_is_extrapolated": extrapolated}


def scene(can_see=True, confidence="high"):
    return {"can_see": can_see, "confidence": confidence}


def main():
    print("the routine case stays on the fast model")
    check("no reasons for an ordinary frame",
          pipeline.escalation_reasons(sensor(), scene()) == [],
          pipeline.escalation_reasons(sensor(), scene()))

    print("\nconditions that justify the larger model")
    r = pipeline.escalation_reasons(sensor(extrapolated=True), scene())
    check("physics beyond its range escalates", "heat_index_extrapolated" in r, r)

    for b in ("danger", "extreme danger"):
        r = pipeline.escalation_reasons(sensor(band=b), scene())
        check("band '{}' escalates".format(b), "high_stakes_band" in r, r)

    for b in ("safe", "caution", "extreme caution"):
        r = pipeline.escalation_reasons(sensor(band=b), scene())
        check("band '{}' does not escalate by itself".format(b), "high_stakes_band" not in r, r)

    r = pipeline.escalation_reasons(sensor(), scene(can_see=True, confidence="low"))
    check("a borderline view escalates", "low_confidence_view" in r, r)

    r = pipeline.escalation_reasons(sensor(), scene(), {"errors": {"heat_advisory": "boom"}})
    check("a failed external lookup escalates", "grounding_incomplete" in r, r)

    print("\ncases that must NOT escalate")
    r = pipeline.escalation_reasons(sensor(), scene(can_see=False, confidence="low"))
    check("a blind frame does not escalate: it abstains instead",
          "low_confidence_view" not in r, r)
    r = pipeline.escalation_reasons(sensor(), scene(), {"errors": {}})
    check("successful grounding does not escalate", r == [], r)

    print("\nreasons accumulate rather than overwrite")
    r = pipeline.escalation_reasons(sensor(band="extreme danger", extrapolated=True),
                                    scene(confidence="low"), {"errors": {"x": "y"}})
    check("all four conditions reported", len(r) == 4, r)

    print("\nevery reason has a human explanation")
    keys = [k for k, _ in pipeline.ESCALATION_TRIGGERS]
    for k in ("heat_index_extrapolated", "high_stakes_band", "low_confidence_view",
              "grounding_incomplete", "model_requested"):
        check("'{}' is documented".format(k), k in keys, keys)

    print("\nthe no-frame case is distinguishable from an unreadable frame")
    check("no-frame scene cannot see", pipeline.NO_FRAME_SCENE["can_see"] is False)
    check("and says why", "no frame" in pipeline.NO_FRAME_SCENE["obstruction"],
          pipeline.NO_FRAME_SCENE)

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all routing checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
