"""External grounding through Tavily.

What the sensor cannot know: whether the met department has issued a heat advisory,
whether outdoor work or schools have been suspended, and whether the power is scheduled
to be off. That last one is not incidental in Pakistan. No power means no fan, and a
fan is often the only cooling a person has, so a load-shedding window turns an
uncomfortable afternoon into a dangerous one.

This is deliberately NOT a research agent that cites its sources. About 30% of the
visible hackathon field built exactly that, so it arrives fortieth on a judge's queue.
Here the retrieved facts change the advice and then get out of the way.

SECURITY NOTE, and it is worth saying out loud in the writeup. Retrieved web text goes
into a model prompt. A page could contain text shaped like an instruction, and a model
that cannot tell retrieved content from its own orders will follow it. So everything
returned here is truncated, stripped, and handed to the reasoner inside an explicitly
labelled data block that the system prompt is told never to obey. This is data, never
instructions.
"""

import json
import os
import re
import urllib.error
import urllib.request

TAVILY_URL = "https://api.tavily.com/search"
KEY_NAMES = ("TAVILY_KEY_NEBIUS", "TAVILY_API_KEY", "TAVILY_KEY")

MAX_SNIPPET = 400      # per result, enough to carry a fact, too short to carry a script
MAX_RESULTS = 4


class GroundingError(RuntimeError):
    pass


def _from_env_file(names):
    """First match by NAME PREFERENCE, not by line order.

    This was wrong until 2026-09-24 and it cost real time. The loop used to walk the
    file and return the first line whose name was recognised, so a stale key sitting
    earlier in .env silently beat the preferred name further down. A .env that
    accumulates keys is the normal case, not the exception, so preference has to win.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        path = os.path.join(here, ".env")
        if os.path.isfile(path):
            found = {}
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, value = line.partition("=")
                    name = name.strip()
                    if name in names and name not in found:
                        found[name] = value.strip().strip('"').strip("'")
            for name in names:                # preference order, not file order
                if found.get(name):
                    return found[name]
        here = os.path.dirname(here)
    return None


def load_key():
    for name in KEY_NAMES:                     # environment wins over .env
        if os.environ.get(name):
            return os.environ[name].strip()
    key = _from_env_file(KEY_NAMES)
    if not key:
        raise GroundingError("no Tavily key. Set one of: " + ", ".join(KEY_NAMES))
    return key


# Phrases that only appear when retrieved text is trying to talk to the model rather
# than inform it. Their presence does not prove an attack, so the snippet is dropped
# rather than the whole result set, and the drop is reported instead of hidden.
_INJECTION = re.compile(
    r"(ignore (all |the )?(previous|above|prior)|disregard (the |all )?(previous|above)"
    r"|system prompt|you are now|new instructions?|act as|jailbreak"
    r"|<\|.*?\|>|\{\{.*?\}\})",
    re.I | re.S,
)


def _clean(text):
    """Flatten retrieved text to something safe to place in a prompt."""
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_SNIPPET]


def search(query, key=None, max_results=MAX_RESULTS, days=None, timeout=45):
    """One Tavily search. Returns (results, dropped_count)."""
    payload = {"query": query, "max_results": max_results, "search_depth": "basic"}
    if days:
        payload["days"] = days
        payload["topic"] = "news"
    req = urllib.request.Request(TAVILY_URL, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + (key or load_key()))
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise GroundingError("Tavily HTTP {}: {}".format(exc.code, exc.read().decode()[:300]))
    except urllib.error.URLError as exc:
        raise GroundingError("Tavily network error: {}".format(exc.reason))

    out, dropped = [], 0
    for res in body.get("results", []):
        snippet = _clean(res.get("content"))
        if not snippet:
            continue
        if _INJECTION.search(snippet):
            dropped += 1
            continue
        out.append({
            "title": _clean(res.get("title"))[:140],
            "url": res.get("url", ""),
            "snippet": snippet,
        })
    return out, dropped


# A search engine returns its best matches whether or not any are relevant. Measured
# 2026-09-24: querying for an Islamabad heat advisory in late September, when the city
# is 23 C and no advisory exists, returned political and military news. Passing that to
# the reasoner as "advisory context" is worse than passing nothing, because the model
# would treat it as evidence about the weather.
#
# So a result must actually be about the thing it was retrieved for. If nothing is,
# the honest answer is "no current advisory found", which is itself useful: it is the
# difference between "there is no warning" and "we did not look".
RELEVANCE = {
    "heat_advisory": ("heat", "temperature", "hot", "warm", "heatwave", "heat wave",
                      "celsius", "weather", "forecast", "meteorolog"),
    "work_or_school_closure": ("heat", "closure", "closed", "suspend", "outdoor work",
                               "school", "working hours", "shift"),
    "load_shedding": ("load shedding", "loadshedding", "power", "outage", "electricity",
                      "iesco", "feeder", "blackout"),
}


# Topical relevance is not enough. The same query returned a heat-and-work article
# about Chicago, which is on subject and useless to someone standing in Islamabad.
# A local advisory has to be about this place.
LOCAL_TERMS = ("islamabad", "rawalpindi", "pakistan", "punjab", "pmd", "ndma", "iesco")


def _relevant(name, result, require_local=True):
    terms = RELEVANCE.get(name)
    blob = (result.get("title", "") + " " + result.get("snippet", "")).lower()
    if terms and not any(term in blob for term in terms):
        return False
    if require_local and not any(term in blob for term in LOCAL_TERMS):
        return False
    return True


def gather(city="Islamabad", key=None, use_judge=False, model_key=None):
    """Everything external that could change today's advice.

    Returns a dict safe to hand to the reasoner, including what was dropped and what
    failed. A silent empty result and a failed lookup must not look the same: the
    reasoner is told which it was, so it can say "no advisory found" rather than
    implying there is none.
    """
    key = key or load_key()
    queries = {
        "heat_advisory": "Pakistan Meteorological Department heat wave advisory {} today".format(city),
        "work_or_school_closure": "{} outdoor work or school closure heat notification".format(city),
        "load_shedding": "{} load shedding schedule power outage timings today".format(city),
    }

    out = {"city": city, "sources": {}, "errors": {}, "snippets_dropped": 0,
           "irrelevant_dropped": 0, "nothing_found": []}
    for name, q in queries.items():
        try:
            results, dropped = search(q, key=key, days=7)
            out["snippets_dropped"] += dropped
            if use_judge:
                kept, dropped, meta = judge_relevance(name, results, city=city, key=model_key)
                out["judge"] = meta.get("judge")
                out.setdefault("drop_reasons", {})[name] = dropped[:4]
            else:
                kept = [r for r in results if _relevant(name, r)]
            out["irrelevant_dropped"] += len(results) - len(kept)
            out["sources"][name] = kept
            if not kept:
                # Said explicitly, so the reasoner can report "no advisory found"
                # rather than inferring safety from an empty list.
                out["nothing_found"].append(name)
        except GroundingError as exc:
            # Recorded, not swallowed. A failed lookup must never read as evidence
            # that no advisory exists.
            out["errors"][name] = str(exc)
            out["sources"][name] = []
    return out


# ------------------------------------------------- relevance, judged by Nemotron

# The keyword filter above is fast, free and deterministic, and it is brittle. It
# matches "heat" and misses "mercury soars past 45". It matches "load shedding" and
# misses "IESCO announces feeder maintenance". A list of words cannot tell that a piece
# about Chicago's heat ordinance is useless to a man standing in Islamabad, except by
# accident of which words appear.
#
# So Nemotron judges relevance and the keyword filter becomes the fallback for when the
# model call fails. Both arms were measured on the same results, and the comparison is
# in 07-JUDGE-REVIEW.md rather than asserted here.

_JUDGE_PROMPT = (
    "A rooftop heat sensor in {city}, Pakistan needs current local facts that would "
    "change safety advice for an outdoor worker standing there right now.\n\n"
    "Question being answered: {question}\n\n"
    "For each numbered search result, decide whether it actually answers that question "
    "FOR {city} OR PAKISTAN. A result can be about the right topic and still be useless "
    "because it is about another country. A result can be local and still be useless "
    "because it is about something else entirely.\n\n"
    "{items}\n\n"
    "Reply with a single JSON object and nothing else:\n"
    '{{"keep": [list of the numbers worth keeping], '
    '"why_dropped": {{"number": "a few words"}}}}'
)

QUESTIONS = {
    "heat_advisory": "Is there a current heat warning or unusually high temperature here?",
    "work_or_school_closure": "Have outdoor work hours or schools been changed because of heat here?",
    "load_shedding": "Is the power scheduled to be off here, which would mean no fan?",
}


# Nemotron judges meaning well and does not judge source quality. Measured: it kept a
# result whose entire title was "FOR MORE DETAILS : https://therealtimeofficial. com/...".
# A title that is mostly a URL carries no fact, whatever it is about, and that is cheap
# to detect without a model.
_URLISH = re.compile(r"https?://|www\.|\.com\b|\.pk\b", re.I)


def _has_substance(result):
    title = (result.get("title") or "").strip()
    if len(title) < 12:
        return False
    if _URLISH.search(title):
        return False
    letters = sum(1 for c in title if c.isalpha())
    return letters >= len(title) * 0.5


def judge_relevance(name, results, city="Islamabad", key=None, model=None):
    """Nemotron decides which results are worth passing on. Returns (kept, dropped, meta).

    Falls back to the keyword filter if the model call fails, because a grounding layer
    that returns nothing when a model is slow is worse than one that returns something
    approximate.
    """
    import models  # imported here so grounding stays usable without a Nebius key

    if not results:
        return [], [], {"judge": "none needed"}

    # Cheap structural filter first. Nothing that fails this is worth a model's time.
    junk = [r for r in results if not _has_substance(r)]
    results = [r for r in results if _has_substance(r)]
    if not results:
        return [], [{"title": r.get("title", ""), "why": "no substance, title is a link"}
                    for r in junk], {"judge": "structural filter only"}

    items = "\n".join("{}. {} :: {}".format(i + 1, r["title"], r["snippet"][:220])
                       for i, r in enumerate(results))
    prompt = _JUDGE_PROMPT.format(city=city, question=QUESTIONS.get(name, name), items=items)

    try:
        text, meta = models.chat(model or models.REASONER_FAST,
                                 [{"role": "user", "content": prompt}],
                                 key=key, max_tokens=2500, temperature=0.0)
        verdict = models._json_from(text)
        keep_raw = verdict.get("keep") or []
        keep = {int(n) for n in keep_raw if str(n).strip().isdigit()}
        kept = [r for i, r in enumerate(results) if (i + 1) in keep]
        dropped = [{"title": r["title"], "why": (verdict.get("why_dropped") or {}).get(str(i + 1), "")}
                   for i, r in enumerate(results) if (i + 1) not in keep]
        dropped += [{"title": r.get("title", ""), "why": "no substance, title is a link"}
                    for r in junk]
        return kept, dropped, {"judge": meta["model"], "usage": meta["usage"],
                               "structurally_dropped": len(junk)}
    except Exception as exc:
        kept = [r for r in results if _relevant(name, r)]
        dropped = [{"title": r["title"], "why": "keyword filter, model unavailable"}
                   for r in results if r not in kept]
        return kept, dropped, {"judge": "keyword fallback", "error": str(exc)[:160]}
