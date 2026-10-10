#!/usr/bin/env python3
"""Point this at a folder and it says whether it holds genuine device output.

    python3 tools/verify_data.py /path/to/folder

Checks the fingerprints that real output from saya_node.ino plus ingest.py always has
and that hand-made or generated data almost never does. It does not accuse anyone of
anything. It reports what it sees so nobody has to argue from impressions.
"""

import datetime
import glob
import json
import os
import sys

OK, WARN, BAD = "ok  ", "?   ", "FAIL"


def main(root):
    root = os.path.abspath(root)
    print("Checking: {}\n".format(root))
    issues = 0

    # ---- find the readings file wherever it sits
    cands = (glob.glob(os.path.join(root, "readings.jsonl"))
             + glob.glob(os.path.join(root, "**", "readings.jsonl"), recursive=True))
    if not cands:
        print("{} no readings.jsonl found anywhere under this folder".format(BAD))
        return 1
    rf = cands[0]
    print("readings file: {}".format(os.path.relpath(rf, root)))

    rows = []
    for n, line in enumerate(open(rf, encoding="utf-8", errors="replace"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print("{} line {} is not valid JSON".format(BAD, n)); issues += 1
    if not rows:
        print("{} file has no readings".format(BAD)); return 1

    # ---- 1. volume
    print("\n1. VOLUME")
    print("   {} readings".format(len(rows)))
    if len(rows) < 100:
        print("   {} a 300s interval gives 288 a day. Under 100 is not a run.".format(BAD)); issues += 1
    elif len(rows) < 600:
        print("   {} under ~2 days of data".format(WARN))
    else:
        print("   {} consistent with a multi-day run".format(OK))

    # ---- 2. microseconds: the strongest single tell
    print("\n2. TIMESTAMP GRANULARITY")
    micros = []
    for r in rows:
        ts = r.get("ts_utc", "")
        micros.append(ts.split(".")[1][:6] if "." in ts else "none")
    zero = sum(1 for m in micros if m in ("000000", "none"))
    print("   {} of {} readings have .000000 or no microseconds".format(zero, len(rows)))
    if zero == len(rows):
        print("   {} the firmware stamps real microseconds. All-zero means synthesised.".format(BAD)); issues += 1
    elif zero > len(rows) * 0.1:
        print("   {} unusually many round timestamps".format(WARN))
    else:
        print("   {} varied, as a real clock produces".format(OK))

    # ---- 3. interval
    print("\n3. SAMPLING INTERVAL")
    def parse(ts):
        for f in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.datetime.strptime(ts.replace("Z", "+0000"), f)
            except ValueError:
                pass
        return None
    # PER DEVICE. Two nodes interleaving at 300s each look like 150s gaps in a combined
    # series, and reading that as a failure was a bug in this checker, not in the data.
    import collections as _c
    bydev = _c.defaultdict(list)
    for r in rows:
        t_ = parse(r.get("ts_utc", ""))
        if t_:
            bydev[r.get("device", "?")].append(t_)

    times = sorted(t for v in bydev.values() for t in v)
    bad_dev = 0
    for dev, ts_ in sorted(bydev.items()):
        ts_.sort()
        if len(ts_) < 3:
            continue
        gaps = sorted((b - a).total_seconds() for a, b in zip(ts_, ts_[1:]))
        med = gaps[len(gaps) // 2]
        mark = OK if abs(med - 300) < 90 else BAD
        if mark is BAD:
            bad_dev += 1
        print("   {:24} n={:<6} median {:.0f}s  min {:.0f}s  max {:.0f}s  {}".format(
            dev, len(ts_), med, gaps[0], gaps[-1], mark))
    if bad_dev:
        print("   {} at least one device does not match the 300s interval".format(BAD)); issues += 1
    else:
        print("   {} every device matches REPORT_INTERVAL_S = 300".format(OK))
    if times:
        print("   span: {} to {}  ({:.1f} days), {} device(s)".format(
            times[0].date(), times[-1].date(),
            (times[-1] - times[0]).total_seconds() / 86400, len(bydev)))

    # ---- 4. the file cannot predate its contents
    print("\n4. FILE AGE vs CONTENT")
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(rf), datetime.timezone.utc)
    print("   file written {}".format(mt.strftime("%Y-%m-%d %H:%M UTC")))
    if times and mt < times[-1]:
        print("   {} file was saved BEFORE its newest reading. Impossible for real data.".format(BAD)); issues += 1
    else:
        print("   {} file is at least as new as its contents".format(OK))

    # ---- 5. images
    print("\n5. IMAGES")
    named = [r["image"] for r in rows if r.get("image")]
    imgdirs = [d for d in (os.path.join(root, "images"), os.path.join(root, "Images"), root)
               if os.path.isdir(d)]
    found = present = 0
    sizes, dims = [], set()
    for d in imgdirs:
        for f in glob.glob(os.path.join(d, "*.jp*g")):
            found += 1
            sizes.append(os.path.getsize(f))
            try:
                from PIL import Image
                dims.add(Image.open(f).size)
            except Exception:
                pass
    for n in named:
        if any(os.path.isfile(os.path.join(d, n)) for d in imgdirs):
            present += 1
    print("   {} image files on disk, {} of {} referenced images resolve".format(found, present, len(named)))
    if named and present == 0:
        print("   {} records point at images that do not exist under these names".format(BAD)); issues += 1
    if sizes:
        avg = sum(sizes) / len(sizes) / 1024
        print("   average size {:.0f} KB".format(avg))
        if avg > 300:
            print("   {} an ESP32-CAM JPEG is 30 to 80 KB. This is far larger.".format(BAD)); issues += 1
        else:
            print("   {} consistent with an ESP32-CAM frame".format(OK))
    if dims:
        print("   dimensions seen: {}".format(sorted(dims)))
        valid = {(800, 600), (640, 480), (1600, 1200), (1024, 768), (320, 240)}
        if not (dims & valid):
            print("   {} none match an ESP32-CAM frame size".format(BAD)); issues += 1
        else:
            print("   {} matches an ESP32-CAM frame size".format(OK))

    # ---- 5b. two boards cannot share a microsecond
    print("\n5b. CROSS-DEVICE TIMESTAMP COLLISIONS")
    import re as _re, collections as _c
    stamps = _c.defaultdict(set)
    for d in imgdirs:
        for f in os.listdir(d):
            m = _re.match(r"(\d{8}T\d{12})Z-(.+)-\d+\.jp", f)
            if m:
                stamps[m.group(1)].add(m.group(2))
    shared = [k for k, v in stamps.items() if len(v) > 1]
    print("   {} timestamps shared by more than one device".format(len(shared)))
    if shared:
        print("   {} ingest.py stamps on arrival. Two POSTs cannot share a microsecond.".format(BAD))
        issues += 1
    else:
        print("   {} no collisions".format(OK))

    # ---- 6. plausibility of the values themselves
    print("\n6. VALUES")
    temps = [r["temp_c"] for r in rows if r.get("temp_c") is not None]
    if temps:
        print("   temperature {:.1f} to {:.1f} C".format(min(temps), max(temps)))
        if len(set(temps)) == 1:
            print("   {} every reading identical: dead sensor or synthetic".format(BAD)); issues += 1
        elif len(set(temps)) < len(temps) * 0.2:
            print("   {} very few distinct values for this many readings".format(WARN))
        else:
            print("   {} varied".format(OK))
        if len(temps) > 20:
            steps = [abs(b - a) for a, b in zip(temps, temps[1:])]
            big = sum(1 for s in steps if s > 3.0)
            print("   {} jumps over 3 C between consecutive readings".format(big))

    print("\n" + "=" * 62)
    if issues:
        print("{} check(s) failed. This does not look like device output.".format(issues))
        return 1
    print("No failures. This looks like genuine device output.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "data"))
