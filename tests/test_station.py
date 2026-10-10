#!/usr/bin/env python3
"""Offline tests for the station comparator. No network.

The comparison is the evidence behind the project's central claim, so the things
tested here are the ways a comparison quietly lies: dropping the hours that did not
match, claiming differences smaller than the instrument can resolve, and losing track
of how many observations it is actually speaking for.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import station  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILURES.append(name)


def obs(epoch, temp, dew=None):
    return {"station": "TEST", "name": "Test", "obs_epoch": epoch, "ts_utc": "x",
            "temp_c": temp, "dewpoint_c": dew,
            "humidity_pct": station.rh_from_dewpoint(temp, dew) if dew is not None else None,
            "lat": 33.551, "lon": 72.827, "elevation_m": 542, "raw": "",
            "temp_resolution_c": 1.0}


def row(iso, temp, rh):
    return {"ts_utc": iso, "temp_c": temp, "humidity_pct": rh}


def main():
    print("humidity derived from dewpoint")
    check("saturated air is 100%", abs(station.rh_from_dewpoint(20, 20) - 100.0) < 0.01)
    check("drier air is lower", station.rh_from_dewpoint(30, 10) < station.rh_from_dewpoint(30, 25))
    check("never exceeds 100", station.rh_from_dewpoint(10, 25) <= 100.0)
    check("never below 0", station.rh_from_dewpoint(45, -30) >= 0.0)
    check("rises as dewpoint rises",
          all(station.rh_from_dewpoint(30, d) < station.rh_from_dewpoint(30, d + 1)
              for d in range(-5, 28)))
    check("missing input gives None", station.rh_from_dewpoint(None, 10) is None)

    print("\ndistance")
    check("zero distance", station.haversine_km(33.7, 73.0, 33.7, 73.0) < 0.001)
    d = station.haversine_km(33.7167, 73.0667, 33.551, 72.827)
    check("Islamabad roof to OPIS is tens of km", 20 < d < 40, round(d, 1))
    check("symmetric", abs(station.haversine_km(1, 2, 3, 4) - station.haversine_km(3, 4, 1, 2)) < 1e-9)

    print("\nmatching device readings to station observations")
    o = [obs(1000, 30.0, 20.0), obs(2800, 31.0, 20.0)]
    import datetime

    def iso(e):
        return datetime.datetime.fromtimestamp(e, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    pairs, unmatched = station.pair_readings([row(iso(1010), 33.0, 40.0)], o)
    check("matches the nearest observation", len(pairs) == 1 and pairs[0]["station_temp_c"] == 30.0,
          pairs)
    check("records the time gap", pairs[0]["gap_s"] == 10, pairs[0]["gap_s"])
    check("computes the delta", pairs[0]["delta_c"] == 3.0, pairs[0]["delta_c"])

    print("\nreadings with no nearby observation are reported, never silently dropped")
    pairs, unmatched = station.pair_readings([row(iso(99999), 33.0, 40.0)], o)
    check("no false pairing", len(pairs) == 0, pairs)
    check("the miss is recorded", len(unmatched) == 1, unmatched)
    check("with a reason", "no station observation" in unmatched[0]["why"], unmatched[0])

    print("\na bad timestamp is recorded too")
    pairs, unmatched = station.pair_readings([{"ts_utc": "not-a-time", "temp_c": 30.0}], o)
    check("unparseable time is reported", len(unmatched) == 1 and "timestamp" in unmatched[0]["why"],
          unmatched)

    print("\nthe comparison states what it is speaking for")
    rows = [row(iso(1000), 33.0, 40.0), row(iso(2800), 34.0, 40.0)]
    r = station.compare(rows, o, comparator={"station": "TEST", "distance_km": 28.8})
    check("n is reported", r["n"] == 2, r["n"])
    check("unmatched count is reported", "unmatched" in r)
    check("direction is counted both ways", r["device_warmer_count"] == 2 and r["device_cooler_count"] == 0, r)
    check("band disagreements counted out of a stated total",
          "band_disagreements" in r and "band_comparisons" in r, r)
    check("station resolution is carried", r["station_temp_resolution_c"] == 1.0, r)
    check("sub-resolution differences are counted separately",
          "differences_within_station_resolution" in r, r)
    check("caveats travel with the number", len(r["caveats"]) >= 3, r.get("caveats"))

    print("\nno overlap means no claim, not a zero")
    r = station.compare([row(iso(99999), 33.0, 40.0)], o)
    check("refuses to compare", r["n"] == 0, r)
    check("and says so", "No comparison" in r["verdict"], r.get("verdict"))

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all station checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
