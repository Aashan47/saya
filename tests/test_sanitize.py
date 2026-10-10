#!/usr/bin/env python3
"""Typography guard for model output. Offline, no key needed.

Model text reaches the screen, the video captions and the writeup, so it is bound by
the same rule as everything else written for this project: no em dashes. A live run on
2026-09-23 returned "extreme\u2011caution" unprompted, so this is enforced in code rather
than requested in a prompt.
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
    print("the banned character")
    for raw in ("Seek shade \u2014 the roof is hot.", "a\u2014b", "a \u2014 b"):
        check("em dash removed from " + repr(raw), "\u2014" not in models.sanitize(raw),
              models.sanitize(raw))
    check("em dash becomes a readable break",
          models.sanitize("Seek shade \u2014 the roof is hot.") == "Seek shade, the roof is hot.",
          models.sanitize("Seek shade \u2014 the roof is hot."))

    print("\nother typography a person here would not type")
    check("non-breaking hyphen", models.sanitize("extreme\u2011caution") == "extreme-caution")
    check("smart apostrophe", models.sanitize("It\u2019s hot") == "It's hot")
    check("smart quotes", models.sanitize("\u201chot\u201d") == '"hot"')
    check("ellipsis", models.sanitize("wait\u2026") == "wait...")
    check("non-breaking space", "\u00a0" not in models.sanitize("a\u00a0b"))

    print("\nen dashes: numeric ranges are allowed, prose punctuation is not")
    check("numeric range survives", models.sanitize("30\u201335 C") == "30\u201335 C",
          models.sanitize("30\u201335 C"))
    check("prose en dash removed", "\u2013" not in models.sanitize("Rest \u2013 then drink."),
          models.sanitize("Rest \u2013 then drink."))

    print("\napplied through nested structures, as model output arrives")
    tree = {"message": "Shade \u2014 now", "factors": ["sun\u2011exposed", {"k": "It\u2019s hot"}]}
    out = models.sanitize_tree(tree)
    check("nested dict cleaned", out["message"] == "Shade, now", out["message"])
    check("nested list cleaned", out["factors"][0] == "sun-exposed", out["factors"][0])
    check("deeply nested cleaned", out["factors"][1]["k"] == "It's hot", out["factors"][1]["k"])

    print("\nmalformed JSON from a model is repaired, not discarded")
    bad = ('{"can_see": true, "obstruction": null, "time_of_day": dusk_or_dawn, '
           '"sun": direct, "confidence": high}')
    d = models._json_from(bad)
    check("bare word quoted", d["time_of_day"] == "dusk_or_dawn", d.get("time_of_day"))
    check("real boolean preserved", d["can_see"] is True, d.get("can_see"))
    check("real null preserved", d["obstruction"] is None, d.get("obstruction"))
    check("numbers not quoted", models._json_from('{"n": 3.5}')["n"] == 3.5)
    check("valid JSON untouched", models._json_from('{"a": "b"}') == {"a": "b"})
    check("multi-word bare value", models._json_from('{"k": extreme caution}')["k"] == "extreme caution")

    print("\nleaves non-strings alone")
    check("numbers pass through", models.sanitize_tree({"t": 33.4, "ok": True}) == {"t": 33.4, "ok": True})
    check("None passes through", models.sanitize(None) is None)

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all typography checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
