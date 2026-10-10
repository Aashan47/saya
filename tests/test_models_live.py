#!/usr/bin/env python3
"""Live pipeline test. Costs credits and needs a key, so it is not in run_all.py.

Exercises physics -> perception -> decision on synthetic frames, and asserts the two
behaviours that matter most and are easiest to get silently wrong:

  * a frame the model cannot read must produce an abstention, not confident advice
  * a reading above the heat index chart must not have a figure quoted back
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
sys.path.insert(0, HERE)

import heat            # noqa: E402
import models          # noqa: E402
from make_frames import clear_scene, dark_frame, obscured_frame  # noqa: E402

FAILURES = []
TOKENS = {"prompt": 0, "completion": 0}


def check(name, cond, detail=""):
    print(("    pass  " if cond else "    FAIL  ") + name + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILURES.append(name)


def run(title, frame, temp_c, rh, key):
    print("\n" + "=" * 68)
    print(title)
    sensor = heat.assess(temp_c, rh)
    print("  physics : hi={} extrapolated={} band={}".format(
        sensor["heat_index_c"], sensor["heat_index_is_extrapolated"], sensor["band"]))

    t0 = time.time()
    scene, m1 = models.see(frame, key=key)
    print("  see     : {:.1f}s  can_see={} conf={} obstruction={}".format(
        time.time() - t0, scene.get("can_see"), scene.get("confidence"), scene.get("obstruction")))

    t0 = time.time()
    out, m2 = models.decide(sensor, scene, key=key)
    print("  decide  : {:.1f}s  advise={} risk={} deeper={}".format(
        time.time() - t0, out.get("advise"), out.get("risk"), out.get("needs_deeper_model")))

    for m in (m1, m2):
        TOKENS["prompt"] += m["usage"].get("prompt_tokens", 0)
        TOKENS["completion"] += m["usage"].get("completion_tokens", 0)

    if out.get("abstain_reason"):
        print("  abstain : " + str(out["abstain_reason"]))
    print("  says    : " + str(out.get("message")))
    return sensor, scene, out


def main():
    key = models.load_key()
    print("vision   : " + models.VISION_MODEL)
    print("reasoner : " + models.REASONER_FAST)

    print("\n--- A: clear scene, ordinary hot day")
    _, sc, out = run("A. clear scene  33 C / 45% RH", clear_scene(), 33.0, 45.0, key)
    check("reads the frame", sc.get("can_see") is True, sc.get("can_see"))
    check("gives advice", out.get("advise") is True, out.get("advise"))
    check("says something", len(str(out.get("message") or "")) > 20, out.get("message"))

    print("\n--- B: lens covered or night. THE ABSTENTION CASE")
    _, sc, out = run("B. dark frame  33 C / 45% RH", dark_frame(), 33.0, 45.0, key)
    check("does not claim to see", sc.get("can_see") is False, sc.get("can_see"))
    check("REFUSES to advise", out.get("advise") is False, out.get("advise"))
    check("explains why", bool(out.get("abstain_reason")), out.get("abstain_reason"))

    print("\n--- C: dust or water on the lens")
    _, sc, out = run("C. obscured frame  33 C / 45% RH", obscured_frame(), 33.0, 45.0, key)
    check("does not claim high confidence", sc.get("confidence") != "high", sc.get("confidence"))

    print("\n--- D: monsoon, heat index above the chart")
    sn, sc, out = run("D. clear scene  38 C / 65% RH", clear_scene(), 38.0, 65.0, key)
    check("physics marks it extrapolated", sn["heat_index_is_extrapolated"] is True)
    check("still reports the danger", out.get("risk") in ("danger", "extreme danger"), out.get("risk"))
    msg = str(out.get("message") or "")
    quoted = re.findall(r"\d+(?:\.\d+)?\s*(?:°|degrees?|celsius|\bC\b)", msg, re.I)
    check("does NOT quote an apparent temperature", not quoted, quoted)

    print("\n" + "=" * 68)
    print("tokens: {} prompt, {} completion".format(TOKENS["prompt"], TOKENS["completion"]))
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all live checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
