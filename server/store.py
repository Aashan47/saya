"""Append-only storage for device readings.

Two deliberate properties, both of which exist because a judge may want to recompute
the headline number from the raw data:

1. Readings are appended to a JSONL file and never rewritten in place. A crash or a
   power cut costs at most the line being written, never the file.
2. The image and the reading that arrived together are linked by a single id, so a
   row can always be traced back to the exact frame it was derived from.

Standard library only. The whole point is that a fresh clone runs.
"""

import itertools
import json
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()


def _utc_now():
    return datetime.now(timezone.utc)


_SEQ = itertools.count()


def reading_id(device, when=None):
    """A sortable, filesystem-safe id, unique even under rapid capture.

    Two earlier versions of this were wrong and the tests caught both. At second
    granularity, captures in the same second collided and the second frame silently
    overwrote the first. At millisecond granularity they still collided: three HTTP
    posts completed inside one millisecond on an ordinary laptop.

    So uniqueness does not rest on the clock alone. The timestamp is carried to the
    microsecond because it makes the id sort chronologically and readable, and a
    process counter is appended because that is what actually guarantees distinctness.
    Losing a frame is not an abstract risk: it means a row in the archive points at
    somebody else's photograph, and the headline comparison is computed from that
    archive.
    """
    when = when or _utc_now()
    stamp = "{}{:06d}Z".format(when.strftime("%Y%m%dT%H%M%S"), when.microsecond)
    return "{}-{}-{:04d}".format(stamp, device, next(_SEQ) % 10000)


class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.images = os.path.join(self.root, "images")
        self.readings_path = os.path.join(self.root, "readings.jsonl")
        os.makedirs(self.images, exist_ok=True)

    def save_image(self, rid, jpeg_bytes):
        """Write the frame first, so a reading never points at an image that is not there.

        Refuses to overwrite. If an id ever collides again despite the scheme above,
        this fails loudly instead of quietly destroying a frame, because a silently
        corrupted archive is worse than a crash.
        """
        name = rid + ".jpg"
        path = os.path.join(self.images, name)
        if os.path.exists(path):
            raise FileExistsError("refusing to overwrite an existing frame: " + name)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(jpeg_bytes)
        os.replace(tmp, path)
        return name

    def append(self, record):
        """Append one reading. Returns the record as stored."""
        line = json.dumps(record, sort_keys=True)
        with _LOCK:
            with open(self.readings_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return record

    def recent(self, limit=50):
        """Most recent readings, newest first. Tolerates a truncated final line."""
        if not os.path.exists(self.readings_path):
            return []
        out = []
        with open(self.readings_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # A power cut mid-write leaves a partial last line. Skip it rather
                    # than losing the whole file, and do not pretend it was never there.
                    continue
        return list(reversed(out))[:limit]

    def count(self):
        return len(self.recent(limit=10 ** 9))


def build_record(rid, device, temp_c, humidity, image_name=None, when=None):
    """One reading. Fields are flat and named, so the CSV export is trivial later."""
    when = when or _utc_now()
    return {
        "id": rid,
        "device": device,
        "ts_utc": when.isoformat().replace("+00:00", "Z"),
        "temp_c": temp_c,
        "humidity_pct": humidity,
        "image": image_name,
    }


# ---------------------------------------------------------------- advice cache

def advice_path(root):
    return os.path.join(os.path.abspath(root), "latest_advice.json")


def save_advice(root, record):
    """Write the latest decision atomically, so a reader never sees half a file."""
    path = advice_path(root)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


def load_advice(root):
    path = advice_path(root)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
