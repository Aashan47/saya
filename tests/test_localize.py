#!/usr/bin/env python3
"""The refusal must survive translation. Offline, no key, no API calls.

Measured 2026-09-26: asked to decide AND answer in Urdu in one step, Nemotron Nano
replied "the sun is strong, wear a cap" and Nemotron Super replied "wear a cap in the
sun and keep drinking water", both for a scene they had been told was unreadable. The
same instruction held every time in English. So the decision is made and gated in code,
and this layer only renders a sentence that decision already contains.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import localize  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(("  pass  " if cond else "  FAIL  ") + name + ("" if cond else "  " + repr(detail)))
    if not cond:
        FAILURES.append(name)


def main():
    print("a refusal is a fixed string and never reaches a model")
    out = localize.localize({"advise": False, "risk": "extreme danger",
                             "message": "I cannot see the location."})
    l = out["localized"]
    check("refusal text is the constant", l["message"] == localize.ABSTAIN_TEXT["ur"], l["message"])
    check("and says it was not translated", "not translated" in l["source"], l["source"])
    check("no danger band is shown while refusing", l["band"] is None, l["band"])
    check("the decision itself is untouched", out["advise"] is False)

    print("\nthe same holds for every falsy form of advise")
    for bad in (False, None, 0, "", "false"):
        o = localize.localize({"advise": bad, "risk": "danger", "message": "x"})
        check("advise={!r} still refuses".format(bad),
              o["localized"]["message"] == localize.ABSTAIN_TEXT["ur"])

    print("\nscript contamination is caught, not averaged away")
    devanagari = "زائد احتیاط बरत"
    check("Devanagari detected", "Devanagari" in localize.contamination(devanagari),
          localize.contamination(devanagari))
    check("and rejected as Urdu", not localize.looks_like(devanagari, "ur"))
    clean = "سایے میں رہیں اور پانی پیئیں"
    check("clean Urdu passes", localize.looks_like(clean, "ur"), clean)
    check("clean Urdu has no contamination", localize.contamination(clean) == [])

    print("\nromanized Urdu is not Urdu")
    roman = "saye mein rahen aur paani piyen"
    check("transliteration rejected", not localize.looks_like(roman, "ur"))
    check("English rejected", not localize.looks_like("Stay in the shade", "ur"))
    check("empty rejected", not localize.looks_like("", "ur"))

    print("\nother scripts that must never appear")
    for text, name in (("你好", "CJK"), ("привет", "Cyrillic"),
                       ("বাংলা", "Bengali")):
        check("{} detected".format(name), name in localize.contamination(text),
              localize.contamination(text))

    print("\nthe cascade is distinct models, not the same one retried")
    check("more than one translator", len(localize.TRANSLATORS) > 1, localize.TRANSLATORS)
    check("all distinct", len(set(localize.TRANSLATORS)) == len(localize.TRANSLATORS))
    check("Nemotron is NOT the translator",
          not any("nemotron" in m.lower() for m in localize.TRANSLATORS), localize.TRANSLATORS)

    print("\nan unsupported language fails loudly")
    o = localize.localize({"advise": True, "risk": "safe", "message": "hi"}, lang="xx")
    check("unsupported language reported", "error" in o["localized"], o["localized"])

    print("\n" + "=" * 52)
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("all localization checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
