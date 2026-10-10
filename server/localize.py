"""Render an already-made decision into the reader's language.

**The decision is never made here, and that is the entire point.**

Measured 2026-09-26. Asked to advise in Urdu on a scene it had been told was unreadable,
Nemotron Nano replied "the sun is strong, wear a cap" and Super replied "wear a cap in
the sun and keep drinking water". Both invented conditions they had been told were not
observable. The same instruction held every time in English. Asking a model to decide
and to translate in one step puts the refusal at the mercy of the translation.

So the pipeline is split. `models.decide()` produces a structured decision in English and
the abstention gate is enforced in code. This module only renders the sentence that
decision already contains. If translation fails, the English decision is still correct
and still shown, because a missing translation is a cosmetic problem and a lost refusal
is a safety one.

**Nemotron does not do the translating, and that was measured, not assumed.** Asked for
Urdu, Nemotron Nano produced a non-word ("ساؤنڈر", "sounder" transliterated) and Nemotron
Super mixed **Devanagari** into otherwise Urdu sentences: 0 of 4 clean over four trials,
6 of 6 failures counting earlier sampling. Four other models served on the same platform
were 4 of 4 clean on the identical prompt. So translation runs on `google/gemma-3-27b-it`,
the fastest of those at about a second, and **the contamination check below stays on as a
runtime guard regardless of which model is used.**

This does not weaken the NVIDIA story, it clarifies it: a vision model turns pixels into
text, a translator turns one finished sentence into Urdu, and **every decision in the
system is made by NVIDIA Nemotron.**

⚠️ NOT YET REVIEWED BY A NATIVE SPEAKER. Observed issues in sampling include inconsistent
register (formal aap and informal tu in the same set) and at least one gender agreement
slip. Aashan must read every string before any of this is filmed or submitted.
"""

import re

import models

# Measured 2026-09-26 over four trials each on an identical prompt:
#   nemotron-3-super-120b-a12b     0/4 clean   (Devanagari contamination every time)
#   Qwen3-235B-A22B-Instruct-2507  4/4 clean
#   GLM-5.3                        4/4 clean
#   DeepSeek-V4-Flash-0731         4/4 clean
#   gemma-3-27b-it                 4/4 clean, and the fastest at roughly a second
# A cascade, not a retry. Failures here are deterministic at temperature 0: gemma
# returned romanized Urdu ("dikhawe ki garmi formula ki had se zyaada hai") twice on
# the identical input, despite the prompt forbidding transliteration. Asking the same
# model again is guaranteed to waste a call. Different models fail on different inputs,
# so the fallback is a different model.
TRANSLATORS = (
    "google/gemma-3-27b-it",              # fastest, about a second
    "deepseek-ai/DeepSeek-V4-Flash-0731",
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
)
TRANSLATOR = TRANSLATORS[0]

LANGUAGES = {
    "ur": {
        "name": "Urdu",
        "script_range": ("؀", "ۿ"),
        "note": "plain everyday spoken Urdu as used in Islamabad, not literary Urdu",
        # Left unpinned, the same model alternated between formal aap and informal tum
        # across three consecutive messages. Advice that changes how it addresses you
        # between sentences reads as careless. Pinned to the respectful form, which is
        # the safer default when addressing a stranger at work.
        # ⚠️ AASHAN'S CALL. He is the native speaker; if tum reads better to him for
        # this audience, change it here and nowhere else.
        "register": "Use the respectful 'aap' form consistently (kareyn, piyen, rakhen). "
                    "Never switch to the informal 'tum' or 'tu' form.",
    },
}

# Rendered in code, never asked of a model. A refusal must read identically every time
# and must not depend on a translation call succeeding.
ABSTAIN_TEXT = {
    "ur": "کیمرہ صاف نہیں دیکھ سکتا، اس لیے ابھی کوئی مشورہ نہیں دیا جا سکتا۔",
}

BAND_TEXT = {
    "ur": {
        "safe": "محفوظ",
        "caution": "احتیاط",
        "extreme caution": "زیادہ احتیاط",
        "danger": "خطرناک",
        "extreme danger": "بہت خطرناک",
    },
}


# Scripts that must never appear in Urdu output. Devanagari is the one that actually
# happened: a translation came back with "बरतوा।", Hindi script and a Devanagari danda,
# mixed into an otherwise Urdu sentence. A percentage threshold missed it because the
# sentence was still mostly Arabic-range. So contamination is checked directly, not
# inferred from a ratio.
FORBIDDEN_RANGES = (
    ("\u0900", "\u097F", "Devanagari"),
    ("\u0980", "\u09FF", "Bengali"),
    ("\u0A00", "\u0A7F", "Gurmukhi"),
    ("\u4E00", "\u9FFF", "CJK"),
    ("\u0400", "\u04FF", "Cyrillic"),
)


def contamination(text):
    """Any script that has no business being in the output. Returns a list of names."""
    found = []
    for lo, hi, name in FORBIDDEN_RANGES:
        if any(lo <= c <= hi for c in text):
            found.append(name)
    return found


def _script_fraction(text, lo, hi):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if lo <= c <= hi) / len(letters)


def looks_like(text, lang):
    """Is this genuinely the target script, with nothing else mixed in?"""
    spec = LANGUAGES.get(lang)
    if not spec or not text:
        return False
    if contamination(text):
        return False
    lo, hi = spec["script_range"]
    return _script_fraction(text, lo, hi) >= 0.92


def translate(message, lang="ur", key=None, model=None):
    """Translate one already-decided sentence. Returns (text, error).

    Never returns a translation it cannot verify is in the right script, because a
    silent fallback to English inside an Urdu interface is worse than an honest gap.
    """
    spec = LANGUAGES.get(lang)
    if not spec:
        return None, "unsupported language: {}".format(lang)
    if not message or not message.strip():
        return None, "nothing to translate"

    prompt = (
        "Translate the text between the markers into {}.\n\n"
        "Rules:\n"
        "- {} \n"
        "- {} \n"
        "- Translate only. Do not add advice, do not remove any, do not change the meaning.\n"
        "- If the text says nothing can be advised, your translation must say exactly that "
        "and must not offer any advice of its own.\n"
        "- Write in the Urdu ABJAD SCRIPT (Arabic script). Do NOT write Urdu using Latin "
        "or Roman letters. 'saya mein rahen' is WRONG. 'سائے میں رہیں' is correct.\n"
        "- Output the translation alone. No English, no transliteration, no notes.\n\n"
        "<<<TEXT\n{}\nTEXT>>>"
    ).format(spec["name"], spec["note"], spec.get("register", ""), message.strip())

    try:
        text, _meta = models.chat(model or TRANSLATOR,
                                  [{"role": "user", "content": prompt}],
                                  key=key, max_tokens=3000, temperature=0.0)
    except models.ModelError as exc:
        return None, str(exc)

    text = models.sanitize(re.sub(r"<<<TEXT|TEXT>>>", "", text).strip())

    bad = contamination(text)
    if bad:
        return None, "output contained {} script".format(" and ".join(bad))
    if not looks_like(text, lang):
        # Refuse rather than ship English dressed as a translation.
        return None, "output was not {} script".format(spec["name"])
    return text, None


# Every advice string is different, so verifying a sample once proves nothing about the
# next one. The round trip runs at runtime: translate to Urdu, translate back with a
# DIFFERENT model, and check the meaning survived. This is the check that would have
# caught "پانی پینے کا ٹکڑا لیں", which back-translates as "take a PIECE of drinking
# water" because the model confused break-the-pause with break-the-fragment.
#
# A model checking its own output is not a check, so the verifier is never the translator.
VERIFIER = "deepseek-ai/DeepSeek-V4-Flash-0731"

# Words whose presence or absence changes what a worker does. If the original said it
# and the round trip lost it, or the round trip invented one, the translation is wrong
# in the way that matters, whatever else it got right.
_SAFETY_TERMS = (
    ("water", ("water", "drink", "hydrat")),
    ("shade", ("shade", "shadow", "shelter", "cover")),
    ("stop", ("stop", "cease", "halt", "avoid", "limit", "reduce")),
    ("rest", ("rest", "break", "pause")),
    ("refusal", ("cannot", "can not", "no advice", "unable", "not able")),
)


def _terms_present(text):
    low = (text or "").lower()
    return {name for name, words in _SAFETY_TERMS if any(w in low for w in words)}


def verify_round_trip(english, urdu, key=None, model=None):
    """Translate the Urdu back and check the safety-relevant meaning survived.

    Returns (ok, detail). Never raises: a verifier that is down must not block advice
    that is probably fine, so an unreachable verifier returns ok with a stated reason.
    """
    if not english or not urdu:
        return False, "nothing to verify"
    try:
        back, _ = models.chat(model or VERIFIER, [{"role": "user", "content":
            "Translate this Urdu into plain English. Translate only, add nothing, "
            "interpret nothing.\n\n" + urdu}], key=key, max_tokens=800, temperature=0.0)
    except models.ModelError as exc:
        return True, "verifier unavailable: {}".format(str(exc)[:90])

    back = models.sanitize(back).strip()
    want, got = _terms_present(english), _terms_present(back)
    lost, gained = sorted(want - got), sorted(got - want)
    if lost or gained:
        return False, {"back_translation": back[:220], "lost": lost, "invented": gained}
    return True, {"back_translation": back[:220]}


def localize(decision, lang="ur", key=None, verify=True):
    """Add a translated rendering to a decision without touching the decision.

    The abstention string is constant and rendered in code. Only genuine advice is
    ever sent to a model for translation.
    """
    out = dict(decision)
    if lang not in LANGUAGES:
        out["localized"] = {"lang": lang, "error": "unsupported language"}
        return out

    band = decision.get("risk")
    block = {"lang": lang, "band": BAND_TEXT.get(lang, {}).get(band)}

    if decision.get("advise") is not True:
        # No band while refusing. The sensor reading is still valid, but printing
        # "extreme danger" beside "no advice can be given" reads as a judgement the
        # system has just said it will not make.
        block["band"] = None
        # Constant text. A refusal must read the same every time and must never depend
        # on a model call succeeding.
        block["message"] = ABSTAIN_TEXT[lang]
        block["source"] = "fixed string, not translated"
        out["localized"] = block
        return out

    # Walk the cascade. Each failure is recorded with the model that produced it, so a
    # model that starts failing consistently is visible rather than hidden by a retry.
    message = decision.get("message", "")
    text, attempts, used = None, [], None
    for candidate in TRANSLATORS:
        text, err = translate(message, lang=lang, key=key, model=candidate)
        if text:
            used = candidate
            break
        attempts.append("{}: {}".format(candidate.split("/")[-1], err))

    block["message"] = text
    block["source"] = ("translated by " + used) if used else None

    if text and verify:
        ok, detail = verify_round_trip(message, text, key=key)
        block["verified"] = ok
        block["round_trip"] = detail
        if not ok:
            # Showing English is a cosmetic failure. Showing Urdu that tells someone to
            # do the wrong thing is not. So the translation is withheld.
            block["message"] = None
            block["error"] = "round trip lost or invented safety terms"
            block["fallback"] = "english"
    if attempts:
        block["attempts_failed"] = attempts
    if text is None:
        block["error"] = "no model produced valid {} script".format(LANGUAGES[lang]["name"])
        # Nothing is shown rather than something wrong. The English decision stands on
        # its own and the interface says the translation is unavailable.
        block["fallback"] = "english"
    if err:
        block["error"] = err
        # No translation is shown rather than a bad one. The English decision stands
        # on its own and the interface says the translation is unavailable.
        block["fallback"] = "english"
    out["localized"] = block
    return out
