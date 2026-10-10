#!/usr/bin/env python3
"""Verify the Urdu without a native speaker, by triangulation.

This does not replace Aashan reading the strings. It is designed to catch the errors a
native speaker would catch, so that what reaches him is already clean and his review is
a confirmation rather than a debugging session.

Three independent checks, each catching a different class of error:

  1. **Back-translation, by three models that did not produce the Urdu.** Translate the
     Urdu back to English and compare against the original English. This is what catches
     meaning errors. It is exactly how the earlier "پانی پینے کا ٹکڑا لیں" was caught:
     it back-translates as "take a PIECE of drinking water", because the model had
     confused break-the-pause with break-the-fragment.

  2. **Independent grammar review by three models**, with only errors that two or more
     agree on reported. One model complaining is noise; two agreeing is a signal.

  3. **A safety check on the refusal string specifically.** Its back-translation must
     contain no advice at all. If any model reads the refusal as telling someone to do
     something, the string is wrong no matter how good its grammar is.

Run: python3 tools/verify_urdu.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import localize  # noqa: E402
import models  # noqa: E402

# Deliberately not gemma, which produced the Urdu. A model checking its own output is
# not an independent check.
REVIEWERS = (
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "zai-org/GLM-5.3",
)


def back_translate(urdu, key):
    out = []
    for m in REVIEWERS:
        try:
            text, _ = models.chat(m, [{"role": "user", "content":
                "Translate this Urdu into plain English. Translate only, add nothing, "
                "interpret nothing.\n\n" + urdu}], key=key, max_tokens=800, temperature=0.0)
            out.append((m.split("/")[-1], models.sanitize(text).strip()))
        except Exception as exc:
            out.append((m.split("/")[-1], "ERROR: {}".format(exc)[:120]))
    return out


def grammar_review(urdu, key):
    """Ask each reviewer for concrete errors. Returns {model: [issues]}."""
    prompt = (
        "You are proofreading Urdu written for construction labourers in Islamabad.\n\n"
        "TEXT: {}\n\n"
        "List only real errors: wrong word, wrong spelling, wrong gender agreement, "
        "wrong verb form, or a register that switches between respectful and informal. "
        "Do not suggest stylistic improvements. If the text is correct, say so.\n\n"
        "Reply with a single JSON object and nothing else:\n"
        '{{"errors": [{{"wrong": "the exact wrong text", "should_be": "the correction", '
        '"why": "a few words"}}], "verdict": "clean" or "has errors"}}'
    ).format(urdu)
    out = {}
    for m in REVIEWERS:
        try:
            text, _ = models.chat(m, [{"role": "user", "content": prompt}],
                                  key=key, max_tokens=1500, temperature=0.0)
            v = models._json_from(text)
            out[m.split("/")[-1]] = v.get("errors") or []
        except Exception as exc:
            out[m.split("/")[-1]] = [{"wrong": "", "should_be": "",
                                      "why": "reviewer failed: {}".format(exc)[:80]}]
    return out


def consensus(reviews):
    """Only errors two or more reviewers independently flag. One is noise."""
    counts = {}
    for model, errs in reviews.items():
        seen = set()
        for e in errs:
            k = (e.get("wrong") or "").strip()
            if not k or k in seen:
                continue
            seen.add(k)
            counts.setdefault(k, {"n": 0, "models": [], "fixes": []})
            counts[k]["n"] += 1
            counts[k]["models"].append(model)
            counts[k]["fixes"].append("{}: {}".format(e.get("should_be", ""), e.get("why", "")))
    return {k: v for k, v in counts.items() if v["n"] >= 2}


def main():
    key = models.load_key()
    problems = 0

    print("=" * 74)
    print("FIXED STRINGS. These ship verbatim on every refusal, so they matter most.")
    print("=" * 74)

    checks = [("refusal", localize.ABSTAIN_TEXT["ur"], "I cannot see, so I cannot advise")]
    for band, ur in localize.BAND_TEXT["ur"].items():
        checks.append(("band: " + band, ur, band))

    for label, urdu, expect in checks:
        print("\n### {}\n    {}".format(label, urdu))
        for model, en in back_translate(urdu, key):
            print("    <- {:22} {}".format(model, en[:110]))
        cons = consensus(grammar_review(urdu, key))
        if cons:
            problems += len(cons)
            for wrong, info in cons.items():
                print("    !! {}/3 flag {!r}".format(info["n"], wrong))
                for f in info["fixes"][:2]:
                    print("         {}".format(f[:100]))
        else:
            print("    ok  no error flagged by two or more reviewers")

    print("\n" + "=" * 74)
    print("SAFETY: the refusal must not read as advice in any back-translation")
    print("=" * 74)
    ADVICE_WORDS = ("drink", "shade", "rest", "water", "wear", "avoid", "stay", "seek", "take")
    for model, en in back_translate(localize.ABSTAIN_TEXT["ur"], key):
        hits = [w for w in ADVICE_WORDS if w in en.lower()]
        flag = "LEAKED ADVICE" if hits else "clean"
        if hits:
            problems += 1
        print("  {:22} {:14} {}".format(model, flag, en[:90]))

    print("\n" + "=" * 74)
    if problems:
        print("{} issue(s) need Aashan's eye. Everything above is machine review only.".format(problems))
        return 1
    print("No consensus errors found. STILL REQUIRES A NATIVE SPEAKER before filming.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
