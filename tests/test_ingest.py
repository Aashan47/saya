"""End to end tests for the ingest service, with no hardware involved.

A simulated device posts to a real server on a real socket, so this exercises the same
path the ESP32-CAM will. Standard library only, run with: python3 tests/test_ingest.py
"""

import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import ingest  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print("  pass  {}".format(name))
    else:
        print("  FAIL  {} {}".format(name, detail))
        FAILURES.append(name)


def fake_jpeg():
    """Smallest thing that starts with the JPEG magic. Content is irrelevant here."""
    return b"\xff\xd8\xff" + b"\x00" * 64 + b"\xff\xd9"


def fake_png():
    """A valid PNG, used to prove a non-JPEG body is rejected as a frame."""
    row = b"\x00" + b"\xff\x00\x00" * 4

    def chunk(kind, data):
        b = kind + data
        return struct.pack(">I", len(data)) + b + struct.pack(">I", zlib.crc32(b) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * 4, 9))
            + chunk(b"IEND", b""))


def post(base, query, body=b"", ctype="image/jpeg"):
    req = urllib.request.Request(base + "/api/reading?" + query, data=body, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main():
    tmp = tempfile.mkdtemp(prefix="saya-test-")
    srv = ingest.make_server("127.0.0.1", 0, tmp)
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("test server on {} data={}".format(base, tmp))

    try:
        print("\nreading with a frame")
        code, body = post(base, "device=roof-01&t=34.2&h=41.0", fake_jpeg())
        check("accepted with 201", code == 201, code)
        rec = body.get("stored", {})
        check("temperature stored", rec.get("temp_c") == 34.2, rec.get("temp_c"))
        check("humidity stored", rec.get("humidity_pct") == 41.0, rec.get("humidity_pct"))
        check("frame linked to reading", rec.get("image") == rec.get("id") + ".jpg", rec.get("image"))
        check("timestamp is UTC", str(rec.get("ts_utc", "")).endswith("Z"), rec.get("ts_utc"))

        print("\nframe is retrievable")
        code, blob = get(base, "/images/" + rec["image"])
        check("image served", code == 200 and blob.startswith(b"\xff\xd8\xff"), code)

        print("\nreading with no frame, the camera-failed case")
        code, body = post(base, "device=roof-01&t=30.0&h=50.0")
        check("accepted without a frame", code == 201, code)
        check("image is null", body["stored"]["image"] is None, body["stored"]["image"])

        print("\na non-JPEG body keeps the reading and drops the frame")
        code, body = post(base, "device=roof-01&t=31.0&h=52.0", fake_png(), "image/png")
        check("reading survives", code == 201, code)
        check("frame discarded", body["stored"]["image"] is None, body["stored"]["image"])

        print("\nimplausible values are refused, not stored")
        code, body = post(base, "device=roof-01&t=999&h=41.0")
        check("temperature out of range rejected", code == 400, code)
        code, body = post(base, "device=roof-01&t=30&h=150")
        check("humidity out of range rejected", code == 400, code)
        code, body = post(base, "device=roof-01")
        check("reading with no values rejected", code == 400, code)
        code, body = post(base, "device=../../etc&t=30&h=40")
        check("path-like device name rejected", code == 400, code)

        print("\nreadings come back newest first")
        code, raw = get(base, "/api/readings?limit=10")
        rows = json.loads(raw.decode())["readings"]
        check("three readings stored", len(rows) == 3, len(rows))
        check("newest first", rows[0]["temp_c"] == 31.0, rows[0]["temp_c"])
        check("rejected rows never entered the data", all(r["temp_c"] != 999 for r in rows))

        print("\nthe stored file is valid JSONL a judge could recompute from")
        with open(os.path.join(tmp, "readings.jsonl")) as fh:
            lines = [l for l in fh.read().splitlines() if l.strip()]
        check("one line per reading", len(lines) == 3, len(lines))
        check("every line parses", all(json.loads(l) for l in lines))

        print("\na truncated final line does not destroy the file")
        with open(os.path.join(tmp, "readings.jsonl"), "a") as fh:
            fh.write('{"id": "half-written-row"')
        code, raw = get(base, "/api/readings?limit=10")
        check("survives a partial line", code == 200 and len(json.loads(raw.decode())["readings"]) == 3)

        print("\nrapid captures do not overwrite each other's frames")
        # Regression guard. At second granularity these three collide on one id and two
        # frames are lost. That matters most while filming, when captures are triggered
        # by hand in quick succession.
        ids = []
        for i in range(3):
            code, body = post(base, "device=burst&t=3{}.0&h=40.0".format(i), fake_jpeg())
            ids.append(body["stored"]["id"])
        check("three distinct ids", len(set(ids)) == 3, ids)
        check("ids sort chronologically", ids == sorted(ids), ids)
        stored = os.listdir(os.path.join(tmp, "images"))
        check("no frame overwritten", sum(1 for f in stored if f.startswith("2") and "burst" in f) == 3, stored)

        print("\nservice endpoints")
        code, raw = get(base, "/healthz")
        check("healthz ok", code == 200 and json.loads(raw.decode())["ok"] is True, code)
        code, raw = get(base, "/")
        check("product page renders", code == 200 and b"<title>Saya</title>" in raw, code)
        check("page carries the abstention styling", b"abstain" in raw)
        check("page has no external dependencies",
              b"http://" not in raw.split(b"<script>")[0].replace(b"http://www.w3.org", b""))

        print("\nstate endpoint feeds the page")
        code, raw = get(base, "/api/state")
        st = json.loads(raw.decode())
        check("state endpoint responds", code == 200, code)
        check("carries readings", "readings" in st and len(st["readings"]) > 0, st.keys())
        check("carries an advice slot", "advice" in st, st.keys())
        check("advice is null until one is computed", st["advice"] is None, st["advice"])
        code, raw = get(base, "/nope")
        check("unknown path is 404", code == 404, code)

    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
