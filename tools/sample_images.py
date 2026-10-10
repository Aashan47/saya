#!/usr/bin/env python3
"""Pick one image per hour from a full run, so the handover stays small.

    python3 tools/sample_images.py <run-folder> <output-folder> [--every-hours 1]

Reads readings.jsonl, selects the first reading in each hour that has an image, and
copies just those frames. Leaves readings.jsonl alone: it is small and carries the
headline claim on its own.
"""
import argparse, json, os, shutil, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--every-hours", type=int, default=1)
    a = ap.parse_args()

    rf = os.path.join(a.src, "readings.jsonl")
    if not os.path.isfile(rf):
        print("no readings.jsonl in", a.src); return 1

    rows = [json.loads(l) for l in open(rf, encoding="utf-8") if l.strip()]
    srcimg = next((d for d in (os.path.join(a.src, "images"), os.path.join(a.src, "Images"))
                   if os.path.isdir(d)), None)
    if not srcimg:
        print("no images/ folder in", a.src); return 1

    os.makedirs(a.dst, exist_ok=True)
    seen, copied, missing = set(), 0, 0
    for r in rows:
        if not r.get("image"):
            continue
        ts = r["ts_utc"]                      # 2026-09-27T14:05:00...
        bucket = (ts[:13], int(ts[11:13]) // a.every_hours)
        if bucket in seen:
            continue
        src = os.path.join(srcimg, r["image"])
        if not os.path.isfile(src):
            missing += 1
            continue
        seen.add(bucket)
        shutil.copy2(src, os.path.join(a.dst, r["image"]))
        copied += 1

    size = sum(os.path.getsize(os.path.join(a.dst, f)) for f in os.listdir(a.dst))
    print("copied {} images ({:.1f} MB) into {}".format(copied, size / 1e6, a.dst))
    if missing:
        print("{} referenced images were not found in {}".format(missing, srcimg))
    print("\nNow send: readings.jsonl (the full one) plus this folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
