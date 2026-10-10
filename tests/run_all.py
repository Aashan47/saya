#!/usr/bin/env python3
"""Run every offline test. No API key needed, no hardware needed, no network.

Live model tests are deliberately separate (test_models_live.py) because they cost
credits and need a key. This is the one a judge runs after cloning, and it must pass
on a clean machine with nothing installed.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_heat.py", "test_sanitize.py", "test_flags.py",
          "test_grounding.py", "test_station.py", "test_pipeline.py",
          "test_localize.py",
          "test_typography.py", "test_ingest.py"]


def main():
    results = []
    for suite in SUITES:
        print("\n" + "#" * 62)
        print("# " + suite)
        print("#" * 62)
        r = subprocess.run([sys.executable, os.path.join(HERE, suite)])
        results.append((suite, r.returncode == 0))

    print("\n" + "=" * 62)
    for suite, ok in results:
        print("  {}  {}".format("PASS" if ok else "FAIL", suite))
    failed = [s for s, ok in results if not ok]
    if failed:
        print("\n{} suite(s) failed".format(len(failed)))
        return 1
    print("\neverything passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
