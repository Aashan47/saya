#!/usr/bin/env python3
"""Replay the archived run into a live server, so the screens can be filmed.

The dashboard shows the LATEST reading. The run is historical (27 Sep to 2 Oct), so
without this the screens are empty and there is nothing to film. This posts archived
readings and their real frames into a running ingest.py, in order, at whatever speed
you choose.

**This is a playback of real measurements, not a simulation.** Every value and every
frame came off the device. Only the arrival time is compressed.

    # terminal 1
    python3 server/ingest.py --port 8080 --advise

    # terminal 2
    python3 tools/replay.py --at 14:00          # jump to a hot afternoon, then live
    python3 tools/replay.py --at 02:00          # a night hour: the abstention fires
    python3 tools/replay.py --speed 60          # whole run at 60x, for a timelapse

`--at` is local Pakistan time. Pick the hour you want on screen.
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))


def post(host, port, r, img):
    q = "device={}".format(r["device"])
    if r.get("temp_c") is not None:
        q += "&t={}".format(r["temp_c"])
    if r.get("humidity_pct") is not None:
        q += "&h={}".format(r["humidity_pct"])
    req = urllib.request.Request("http://{}:{}/api/reading?{}".format(host, port, q),
                                 data=img or b"", method="POST")
    req.add_header("Content-Type", "image/jpeg")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(HERE, "..", "data"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--at", help="local PKT hour to start from, e.g. 14:00 or 02:00")
    ap.add_argument("--device", default="islamabad-roof-01")
    ap.add_argument("--speed", type=float, default=0,
                    help="0 = one reading every 8s (filming pace). 60 = 60x real time")
    ap.add_argument("--only-with-frames", action="store_true", default=True)
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(os.path.join(a.data, "readings.jsonl")) if l.strip()]
    rows = [r for r in rows if r["device"] == a.device]
    if a.only_with_frames:
        rows = [r for r in rows if r.get("image")]
    rows.sort(key=lambda r: r["ts_utc"])
    if not rows:
        print("no readings for device {}".format(a.device)); return 1

    if a.at:
        want = int(a.at.split(":")[0])
        idx = next((i for i, r in enumerate(rows)
                    if (int(r["ts_utc"][11:13]) + 5) % 24 == want), 0)
        rows = rows[idx:]
        print("starting at local {:02d}:00".format(want))

    print("replaying {} readings from {} into http://{}:{}".format(
        len(rows), a.device, a.host, a.port))
    print("every value and frame is real. only the timing is compressed.\n")

    prev = None
    for n, r in enumerate(rows, 1):
        img = None
        if r.get("image"):
            p = os.path.join(a.data, "images", r["image"])
            if os.path.isfile(p):
                img = open(p, "rb").read()
        try:
            code = post(a.host, a.port, r, img)
        except urllib.error.URLError as e:
            print("cannot reach the server. Is ingest.py running? ({})".format(e.reason))
            return 1
        local = (int(r["ts_utc"][11:13]) + 5) % 24
        print("  {:>4}/{}  {} local {:02d}:xx  {:.1f} C  {:.0f}%  {}  -> {}".format(
            n, len(rows), r["ts_utc"][:10], local, r["temp_c"], r["humidity_pct"],
            "frame" if img else "no frame", code))

        if a.speed:
            import datetime
            t = datetime.datetime.strptime(r["ts_utc"][:19], "%Y-%m-%dT%H:%M:%S")
            if prev:
                time.sleep(max(0, (t - prev).total_seconds() / a.speed))
            prev = t
        else:
            time.sleep(8)        # slow enough that the advice lands before the next post
    return 0


if __name__ == "__main__":
    sys.exit(main())
