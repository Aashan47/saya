#!/usr/bin/env python3
"""Process the accumulated sensor archive on Nebius, and produce the headline result.

The Physical AI track recommends Nebius Serverless Jobs to "process sensor data at
scale". **Managed batch inference is not available on a hackathon account**: a
`batch_inference` operation returns 403 while `file_to_dataset` succeeds on the same
account and the dataset it creates reads READY. That is measured, not assumed, and it
is written up in 01-HACKATHON-FACTS.md.

So the archive is staged on Nebius through the datasets API, which does work, and
processed with concurrent completions against Token Factory. The scale is real even if
the managed job is not: a device reporting every five minutes produces 288 readings a
day, and a three week run is around 6,000 frames that no one is going to review by hand.

What comes out:
  * every reading re-judged, so the whole archive is consistent with the current model
  * the device versus station comparison, which is the project's central claim
  * the abstention rate, which says how often the system admitted it could not see

Run: python3 tools/process_archive.py --data data [--limit 50] [--workers 6]
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import heat        # noqa: E402
import models      # noqa: E402
import pipeline    # noqa: E402
import station     # noqa: E402
import store as store_mod  # noqa: E402


def load_rows(data_dir, limit=None):
    st = store_mod.Store(data_dir)
    rows = list(reversed(st.recent(limit=10 ** 9)))     # oldest first
    if limit:
        rows = rows[-limit:]
    return st, rows


def judge_one(st, row, key):
    """Re-run one archived reading through the full decision. Never raises."""
    jpeg = None
    if row.get("image"):
        path = os.path.join(st.images, row["image"])
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                jpeg = fh.read()
    try:
        # Grounding and the station lookup are deliberately off here. Both describe
        # right now, and replaying them across an archive would stamp today's outage
        # onto a reading from three weeks ago.
        out = pipeline.advise(row["temp_c"], row["humidity_pct"], jpeg_bytes=jpeg,
                              key=key, with_station=False, with_grounding=False, lang=None)
        return {"id": row["id"], "ts_utc": row["ts_utc"], "ok": True,
                "band": out["sensor"]["band"],
                "advised": out["decision"].get("advise") is True,
                "can_see": out["scene"].get("can_see"),
                "escalated": out["routing"]["escalated"],
                "decided_by": out["routing"]["decided_by"],
                "message": out["decision"].get("message")}
    except Exception as exc:
        return {"id": row["id"], "ts_utc": row.get("ts_utc"), "ok": False,
                "error": "{}: {}".format(type(exc).__name__, exc)[:160]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(HERE, "..", "data"))
    ap.add_argument("--limit", type=int, help="only the most recent N readings")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--with-images-only", action="store_true",
                    help="only judge readings that have a frame. The vision and abstention "
                         "analysis is meaningless without one, and this avoids spending a "
                         "model call to conclude 'no frame' thousands of times.")
    ap.add_argument("--out", default=None, help="where to write the report")
    args = ap.parse_args()

    key = models.load_key()
    st, rows = load_rows(args.data, args.limit)
    station_rows = list(rows)          # the comparison uses EVERY reading, frame or not
    if args.with_images_only:
        rows = [r for r in rows if r.get("image")]
        print("judging only the {} readings that carry a frame".format(len(rows)))
    if not rows:
        print("No readings in {}. Nothing to process.".format(os.path.abspath(args.data)))
        return 1

    print("archive: {} readings, {} with a frame".format(
        len(rows), sum(1 for r in rows if r.get("image"))))
    print("processing with {} concurrent workers on Token Factory\n".format(args.workers))

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(judge_one, st, r, key): r for r in rows}
        for n, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if n % 5 == 0 or n == len(rows):
                print("  {}/{}  {:.0f}s elapsed".format(n, len(rows), time.time() - t0))
    elapsed = time.time() - t0
    results.sort(key=lambda r: r.get("ts_utc") or "")

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    advised = [r for r in ok if r["advised"]]
    abstained = [r for r in ok if not r["advised"]]
    escalated = [r for r in ok if r["escalated"]]

    # The comparison. This is the claim, so it carries its own caveats.
    comparison = {"n": 0, "verdict": "no station data"}
    try:
        obs = station.metar(hours=24 * 14)
        comp_ctx = station.describe_comparator(pipeline.DEVICE_LAT, pipeline.DEVICE_LON, obs)
        comparison = station.compare(station_rows, obs, comparator=comp_ctx)
    except station.StationError as exc:
        comparison = {"n": 0, "verdict": "station unreachable: {}".format(exc)[:140]}

    # The gap by hour, which is where the claim either lives or dies.
    hourly, working, band_hours = {}, {}, {}
    try:
        obs_for_pairs = station.metar(hours=24 * 14)
        pairs, _unmatched = station.pair_readings(station_rows, obs_for_pairs)
        hourly = station.by_hour(pairs)
        working = station.working_hours_summary(pairs)
        band_hours = station.band_disagreements_by_hour(pairs)
    except station.StationError:
        pass

    # A breakdown of WHY it declined. An abstention rate alone says nothing: a system
    # that refuses all night because it is dark is behaving correctly, and one that
    # refuses at noon has a dirty lens. Those must not be reported as the same number.
    causes = {"could_not_see": 0, "no_frame": 0, "other": 0}
    for r in ok:
        if r["advised"]:
            continue
        if r.get("can_see") is False:
            causes["could_not_see"] += 1
        elif r.get("can_see") is None:
            causes["no_frame"] += 1
        else:
            causes["other"] += 1

    report = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "abstention_causes": causes,
        "gap_by_local_hour": hourly,
        "working_hours": working,
        "band_disagreements_by_hour": band_hours,
        "readings_processed": len(rows),
        "seconds": round(elapsed, 1),
        "readings_per_minute": round(len(rows) / (elapsed / 60.0), 1) if elapsed else None,
        "failures": len(failed),
        "advised": len(advised),
        "abstained": len(abstained),
        "abstention_rate": round(len(abstained) / len(ok), 3) if ok else None,
        "escalated_to_super": len(escalated),
        "bands": {b: sum(1 for r in ok if r["band"] == b)
                  for b in sorted({r["band"] for r in ok})},
        "device_vs_station": comparison,
    }

    print("\n" + "=" * 68)
    print("ARCHIVE REPORT")
    print("=" * 68)
    print("  processed        : {} readings in {:.0f}s ({} a minute)".format(
        report["readings_processed"], elapsed, report["readings_per_minute"]))
    print("  failed           : {}".format(report["failures"]))
    print("  advised          : {}".format(report["advised"]))
    print("  abstained        : {}  ({})".format(
        report["abstained"],
        "{:.0%}".format(report["abstention_rate"]) if report["abstention_rate"] is not None else "n/a"))
    print("  escalated        : {} to Nemotron Super".format(report["escalated_to_super"]))
    print("  bands            : {}".format(json.dumps(report["bands"])))
    c = comparison
    if c.get("n"):
        print("\n  device vs {} ({} km away, {} m up)".format(
            c["comparator"]["station"], c["comparator"]["distance_km"],
            c["comparator"]["station_elevation_m"]))
        print("    matched pairs  : {}   unmatched: {}".format(c["n"], c["unmatched"]))
        print("    mean difference: {:+.2f} C   max: {:+.2f} C".format(
            c["mean_delta_c"], c["max_delta_c"]))
        print("    device warmer  : {} of {}".format(c["device_warmer_count"], c["n"]))
        print("    inside station resolution, so not evidence: {}".format(
            c["differences_within_station_resolution"]))
        print("    ADVISORY BANDS THAT DISAGREED: {} of {}".format(
            c["band_disagreements"], c["band_comparisons"]))
    else:
        print("\n  device vs station: {}".format(c.get("verdict")))

    if working and working.get("n_working"):
        print("\n  WHEN IT MATTERS ({})".format(working["working_hours"]))
        print("    working hours : {:+.2f} C over {} readings".format(
            working["mean_delta_working_c"], working["n_working"]))
        if working.get("mean_delta_other_c") is not None:
            print("    other hours   : {:+.2f} C over {} readings".format(
                working["mean_delta_other_c"], working["n_other"]))
            print("    difference    : {:+.2f} C".format(working["day_night_difference_c"]))
            print("    ^ If this is near zero the gap is a flat offset, not a microclimate")
            print("      effect, and it must be reported as an offset.")

    if hourly:
        print("\n  GAP BY LOCAL HOUR (device minus station)")
        for h, v in hourly.items():
            bar = "#" * min(40, int(abs(v["mean_delta_c"]) * 6))
            print("    {:02d}:00  {:+6.2f} C  n={:<4} {}".format(h, v["mean_delta_c"], v["n"], bar))

    if band_hours:
        worst = sorted(band_hours.items(), key=lambda kv: -kv[1]["disagreed"])[:4]
        print("\n  HOURS WHERE THE STATION WOULD HAVE GIVEN A DIFFERENT BAND")
        for h, v in worst:
            if not v["disagreed"]:
                continue
            print("    {:02d}:00  {} of {} readings differed, device worse in {}".format(
                h, v["disagreed"], v["n"], v["device_worse"]))
            for e in v["examples"][:1]:
                print("           e.g. station said '{}', device said '{}'".format(
                    e["station"], e["device"]))

    print("\n  ABSTENTION CAUSES")
    for k, v in causes.items():
        print("    {:16} {}".format(k, v))

    out_path = args.out or os.path.join(args.data, "archive_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"report": report, "per_reading": results}, fh, indent=2, default=str)
    print("\n  written to {}".format(os.path.abspath(out_path)))
    print("\n  Every number above is recomputable from data/readings.jsonl and the raw")
    print("  METAR reports. Report the days measured and nothing beyond them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
