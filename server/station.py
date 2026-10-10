"""The official comparator: what the nearest real weather station says.

The project's central claim is that air temperature where people actually work differs
from the official observation used to issue advisories. That claim needs a denominator,
and this is it.

**OPIS, Islamabad/Gandhara International**, published as METAR through
aviationweather.gov. Free, no key, roughly half-hourly, and it is a genuine official
station observation rather than a model output. Verified working 2026-09-24.

A second, weaker comparator is Open-Meteo, which is a gridded model and reanalysis
blend rather than a station. It is kept because a second arm that disagrees is worth
reporting, and because it fills gaps when METAR is late.

Three honest limitations, all of which belong in the writeup rather than being
discovered by a reviewer:

  1. **METAR reports temperature in whole degrees Celsius.** A 24 in the feed means
     somewhere in [23.5, 24.5). Differences smaller than about 1 C are inside the
     station's own resolution and must not be claimed.
  2. **Relative humidity is derived, not reported.** METAR gives dewpoint, so RH comes
     from the August-Roche-Magnus relation below and inherits the same 1 C granularity.
  3. **The station is not next door and not at the same height.** It sits at 542 m
     elevation some tens of kilometres from central Islamabad. Part of any difference
     found is geography and altitude, not the urban microclimate the project is about.
     Report the distance and the elevation gap alongside the result.
"""

import json
import math
import urllib.error
import urllib.request

METAR_URL = "https://aviationweather.gov/api/data/metar?ids={}&format=json&hours={}"
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
    "&current=temperature_2m,relative_humidity_2m&timezone=UTC"
)

DEFAULT_ICAO = "OPIS"          # Islamabad/Gandhara Intl. OPRN returns nothing.
USER_AGENT = "saya-heat-advisor/0.1"


class StationError(RuntimeError):
    pass


def _get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise StationError("HTTP {} from {}: {}".format(exc.code, url.split("?")[0],
                                                        exc.read().decode()[:200]))
    except urllib.error.URLError as exc:
        raise StationError("network error: {}".format(exc.reason))
    except json.JSONDecodeError as exc:
        raise StationError("station returned non-JSON: {}".format(exc))


def rh_from_dewpoint(temp_c, dew_c):
    """Relative humidity from temperature and dewpoint, August-Roche-Magnus.

    ⚠️ Like the NOAA coefficients in heat.py, these constants are written from general
    knowledge rather than transcribed from a source opened for this build. The property
    tests check what must be true of any correct form. Verify before quoting.
    """
    if temp_c is None or dew_c is None:
        return None
    a, b = 17.625, 243.04
    num = math.exp((a * dew_c) / (b + dew_c))
    den = math.exp((a * temp_c) / (b + temp_c))
    return max(0.0, min(100.0, 100.0 * num / den))


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance. Used to state honestly how far away the comparator is."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def metar(icao=DEFAULT_ICAO, hours=6):
    """Recent observations from one station, newest first."""
    rows = _get(METAR_URL.format(icao, hours))
    if not rows:
        raise StationError("no observations for {} in the last {}h".format(icao, hours))
    out = []
    for ob in rows:
        t, d = ob.get("temp"), ob.get("dewp")
        out.append({
            "station": ob.get("icaoId"),
            "name": ob.get("name"),
            "ts_utc": ob.get("reportTime"),
            "obs_epoch": ob.get("obsTime"),
            "temp_c": float(t) if t is not None else None,
            "dewpoint_c": float(d) if d is not None else None,
            "humidity_pct": rh_from_dewpoint(t, d),
            "lat": ob.get("lat"),
            "lon": ob.get("lon"),
            "elevation_m": ob.get("elev"),
            # Kept so any number here can be traced back to the original report.
            "raw": ob.get("rawOb"),
            # Stated, not implied: this feed is integer degrees.
            "temp_resolution_c": 1.0,
        })
    out.sort(key=lambda r: r.get("obs_epoch") or 0, reverse=True)
    return out


def open_meteo(lat, lon):
    """Secondary comparator. A gridded model, NOT a station. Labelled as such."""
    d = _get(OPEN_METEO_URL.format(lat=lat, lon=lon))
    cur = d.get("current") or {}
    return {
        "source": "open-meteo",
        "kind": "gridded model and reanalysis blend, not a station observation",
        "ts_utc": cur.get("time"),
        "temp_c": cur.get("temperature_2m"),
        "humidity_pct": cur.get("relative_humidity_2m"),
    }


def describe_comparator(device_lat, device_lon, obs):
    """State plainly how far away the comparator is and how high."""
    if not obs or obs[0].get("lat") is None:
        return None
    o = obs[0]
    return {
        "station": o["station"],
        "name": o["name"],
        "distance_km": round(haversine_km(device_lat, device_lon, o["lat"], o["lon"]), 1),
        "station_elevation_m": o["elevation_m"],
        # Measured 2026-09-27: the roof is ~560 m and OPIS ~535 m, so the roof is about
        # 25 m HIGHER, which is 0.16 C at a standard lapse rate. Altitude is NOT a
        # confounder here. Saying it was conceded ground that did not need conceding.
        "elevation_note": "Station elevation is absolute, not a difference from the device. "
                          "Compute the actual gap before attributing anything to altitude.",
        "caveat": "Distance is 28.8 km, so surroundings differ. Altitude does not: the two "
                  "sites are within tens of metres. Report the real elevation gap, not the "
                  "station's absolute height.",
    }


# --------------------------------------------------------------- the comparison

def _epoch(iso):
    """Parse the ISO timestamps written by store.build_record."""
    import datetime
    return datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z").timestamp() \
        if "." in iso else \
        datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S%z").timestamp()


def pair_readings(device_rows, station_obs, max_gap_s=1800):
    """Match each device reading to the nearest station observation in time.

    Unmatched readings are returned separately rather than dropped. A comparison that
    quietly discards the hours it could not match is a comparison of the hours that
    happened to agree.
    """
    obs = [o for o in station_obs if o.get("obs_epoch") and o.get("temp_c") is not None]
    pairs, unmatched = [], []
    for row in device_rows:
        if row.get("temp_c") is None:
            continue
        try:
            t = _epoch(row["ts_utc"])
        except (ValueError, KeyError):
            unmatched.append({"row": row, "why": "unparseable timestamp"})
            continue
        best = min(obs, key=lambda o: abs(o["obs_epoch"] - t)) if obs else None
        if best is None or abs(best["obs_epoch"] - t) > max_gap_s:
            unmatched.append({"row": row, "why": "no station observation within {}s".format(max_gap_s)})
            continue
        pairs.append({
            "ts_utc": row["ts_utc"],
            "gap_s": int(abs(best["obs_epoch"] - t)),
            "device_temp_c": row["temp_c"],
            "device_humidity_pct": row.get("humidity_pct"),
            "station_temp_c": best["temp_c"],
            "station_humidity_pct": best.get("humidity_pct"),
            "delta_c": round(row["temp_c"] - best["temp_c"], 2),
        })
    return pairs, unmatched


def compare(device_rows, station_obs, comparator=None):
    """The headline result, with its own limits attached.

    Reports how often an advisory computed from the station alone would have landed in
    a different heat band than one computed where the person actually stands. That is
    the claim the project makes, and it is reported as a count out of a stated n, never
    as a rate dressed up to sound larger.
    """
    import heat

    pairs, unmatched = pair_readings(device_rows, station_obs)
    if not pairs:
        return {
            "n": 0,
            "verdict": "No overlapping observations. No comparison can be made.",
            "unmatched": len(unmatched),
            "comparator": comparator,
        }

    deltas = [p["delta_c"] for p in pairs]
    resolution = station_obs[0].get("temp_resolution_c", 1.0) if station_obs else 1.0

    band_diff = 0
    counted = 0
    for p in pairs:
        if p["device_humidity_pct"] is None or p["station_humidity_pct"] is None:
            continue
        counted += 1
        a = heat.assess(p["device_temp_c"], p["device_humidity_pct"])["band"]
        b = heat.assess(p["station_temp_c"], p["station_humidity_pct"])["band"]
        if a != b:
            band_diff += 1

    return {
        "n": len(pairs),
        "unmatched": len(unmatched),
        "mean_delta_c": round(sum(deltas) / len(deltas), 2),
        "max_delta_c": round(max(deltas, key=abs), 2),
        "device_warmer_count": sum(1 for d in deltas if d > 0),
        "device_cooler_count": sum(1 for d in deltas if d < 0),
        # The number that matters, phrased as a count out of n.
        "band_disagreements": band_diff,
        "band_comparisons": counted,
        "station_temp_resolution_c": resolution,
        "differences_within_station_resolution": sum(1 for d in deltas if abs(d) < resolution),
        "comparator": comparator,
        "caveats": [
            "The station reports whole degrees Celsius, so any difference smaller than "
            "{} C is inside its own resolution and is not evidence.".format(resolution),
            "Station humidity is derived from dewpoint, not measured directly.",
            "Distance and elevation account for part of any difference. See comparator.",
        ],
    }


# ------------------------------------------------- when the gap actually matters

def _hour_of(iso):
    """Local hour in Pakistan (UTC+5). Advisories are lived locally, not in UTC."""
    import datetime
    try:
        t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z" if "." in iso
                                       else "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        return None
    return (t + datetime.timedelta(hours=5)).hour


def by_hour(pairs):
    """The gap broken down by local hour.

    A mean difference over a whole run is the weakest claim available and the easiest
    to wave away: night and day cancel, and a roof that is hotter at 3pm and cooler at
    4am averages to nothing interesting.

    What matters is whether the gap is largest **when people are actually working in
    it**. If the device runs warmer than the station specifically through the working
    afternoon, that is a finding. If the gap is flat across the day, it is an offset
    and should be reported as one.
    """
    buckets = {}
    for p in pairs:
        h = _hour_of(p["ts_utc"])
        if h is None:
            continue
        buckets.setdefault(h, []).append(p["delta_c"])

    out = {}
    for h, deltas in sorted(buckets.items()):
        out[h] = {
            "n": len(deltas),
            "mean_delta_c": round(sum(deltas) / len(deltas), 2),
            "max_delta_c": round(max(deltas, key=abs), 2),
        }
    return out


WORKING_HOURS = range(9, 18)   # when an outdoor labourer is actually exposed


def working_hours_summary(pairs):
    """The claim that matters, stated over the hours it applies to."""
    day = [p["delta_c"] for p in pairs if (_hour_of(p["ts_utc"]) or -1) in WORKING_HOURS]
    night = [p["delta_c"] for p in pairs if (_hour_of(p["ts_utc"]) or -1) not in WORKING_HOURS]
    if not day:
        return {"verdict": "no readings inside working hours"}
    out = {
        "working_hours": "09:00 to 17:59 local",
        "n_working": len(day),
        "mean_delta_working_c": round(sum(day) / len(day), 2),
        "n_other": len(night),
        "mean_delta_other_c": round(sum(night) / len(night), 2) if night else None,
    }
    if night:
        # Reported whichever way it comes out. A device that runs cooler at night and
        # hotter by day is a more credible story than one that is hotter always, and
        # pretending otherwise is the failure this project exists to avoid.
        out["day_night_difference_c"] = round(out["mean_delta_working_c"]
                                              - out["mean_delta_other_c"], 2)
    return out


def band_disagreements_by_hour(pairs):
    """When would an advisory from the station alone have been in the wrong band?

    This is the actionable version of the claim. "The bands differed on 13 of 18
    readings" is abstract. "The station said caution while the device said danger, and
    it happened between 1pm and 4pm" is a thing a labour inspector can act on.
    """
    import heat
    rows = {}
    for p in pairs:
        if p["device_humidity_pct"] is None or p["station_humidity_pct"] is None:
            continue
        h = _hour_of(p["ts_utc"])
        if h is None:
            continue
        dev = heat.assess(p["device_temp_c"], p["device_humidity_pct"])["band"]
        sta = heat.assess(p["station_temp_c"], p["station_humidity_pct"])["band"]
        r = rows.setdefault(h, {"n": 0, "disagreed": 0, "device_worse": 0, "examples": []})
        r["n"] += 1
        if dev != sta:
            r["disagreed"] += 1
            order = ["safe", "caution", "extreme caution", "danger", "extreme danger"]
            if order.index(dev) > order.index(sta):
                r["device_worse"] += 1
            if len(r["examples"]) < 2:
                r["examples"].append({"ts_utc": p["ts_utc"], "station": sta, "device": dev})
    return dict(sorted(rows.items()))
