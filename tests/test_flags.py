#!/usr/bin/env python3
"""The abstention gate must not be defeated by a string. Offline, no key needed.

Measured on 2026-09-23: the same model returned a JSON boolean for `advise` in three
cases and the STRING "true" in a fourth. In Python the string "false" is truthy, so
reading such a field directly would give advice at the precise moment the model was
refusing to. Every decision-gating field is therefore coerced, and anything unrecognised
resolves to the cautious answer.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import models  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILURES.append(name)


def main():
    print("the hazard: truthy strings that mean false")
    for raw in ("false", "False", "FALSE", "no", "n", "0", "null", "none"):
        check("{!r} reads as False".format(raw), models.as_bool(raw) is False, models.as_bool(raw))

    print("\ngenuine affirmatives still read as True")
    for raw in ("true", "True", "TRUE", "yes", "y", "1", True, 1):
        check("{!r} reads as True".format(raw), models.as_bool(raw) is True, models.as_bool(raw))

    print("\nanything unrecognised fails closed")
    for raw in ("maybe", "unknown", "", None, [], {}, 3.7j if False else "probably"):
        check("{!r} fails closed".format(raw), models.as_bool(raw) is False, models.as_bool(raw))

    print("\nthe real shape: a refusal expressed as a string")
    out = models.coerce_flags({"advise": "false", "abstain_reason": "cannot see"},
                              ("advise",))
    check("advise is a real boolean", out["advise"] is False, out["advise"])
    check("and gates correctly", not out["advise"], out["advise"])

    scene = models.coerce_flags({"can_see": "false", "shade_available": "true"},
                                ("can_see", "shade_available"))
    check("can_see coerced", scene["can_see"] is False, scene["can_see"])
    check("other flags coerced too", scene["shade_available"] is True, scene["shade_available"])

    print("\nabsent fields do not become accidental permission")
    check("missing advise is not truthy", models.as_bool({}.get("advise")) is False)
    check("missing can_see is not truthy", models.as_bool({}.get("can_see")) is False)

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all abstention-gate checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
