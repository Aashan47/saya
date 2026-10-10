#!/usr/bin/env python3
"""Gate 1: does a Nemotron Omni model accept images through Nebius Token Factory?

The original architecture rested on this, so it was run before anything else was built. Standard library only, so there is nothing to install.

What it does, in order:

1. GET /v1/models and print every Nemotron id the account can actually see. The Omni
   model id was not published anywhere we could find, so it is discovered here rather
   than guessed.
2. Build a small PNG in memory whose left and right halves are two randomly chosen
   colours, send it as a data URI, and ask the model to name them.
3. Compare the answer to the colours we actually drew.

Point 3 is the part that matters. An endpoint can accept an image field, silently drop
it, and still answer fluently. Because the colours are random per run, a text-only model
that never saw the image has to guess: with 4 colours and 2 distinct picks that is a
1 in 12 chance of a false pass. So a single PASS is strong but not proof. Use --repeat 3
if you want the false-pass probability down near 1 in 1700.

Usage:
    export NEBIUS_API_KEY=...          # or put it in a .env file in a parent folder
    python3 omni_image_probe.py --list
    python3 omni_image_probe.py
    python3 omni_image_probe.py --model nvidia/nemotron-3-nano-omni --repeat 3

The key is read from the environment, falling back to .env. It is never printed.
"""

import argparse
import base64
import json
import os
import random
import struct
import sys
import urllib.error
import urllib.request
import zlib

BASE_URL = "https://api.tokenfactory.nebius.com/v1"

# Saturated and far apart, so there is no reasonable disagreement about the name.
COLOURS = {
    "red": (220, 20, 20),
    "green": (20, 200, 20),
    "blue": (20, 40, 220),
    "yellow": (240, 220, 20),
}


# NEBUIS_API_KEY is a transposition of NEBIUS, and it is a spelling we
# actually used. Accept it rather than silently reporting "no key found".
KEY_NAMES = ("NEBIUS_API_KEY", "NEBUIS_API_KEY", "NEBIUS_TOKEN_FACTORY_KEY")


def load_key():
    for name in KEY_NAMES:
        key = os.environ.get(name)
        if key:
            return key.strip()
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        candidate = os.path.join(here, ".env")
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, value = line.partition("=")
                    if name.strip() in KEY_NAMES:
                        return value.strip().strip('"').strip("'")
        here = os.path.dirname(here)
    return None


def png_two_halves(left_rgb, right_rgb, size=96):
    """Encode a size x size PNG split vertically into two solid colours."""
    half = size // 2
    row = bytes(left_rgb) * half + bytes(right_rgb) * (size - half)
    raw = b"".join(b"\x00" + row for _ in range(size))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def call(path, key, payload=None, timeout=120):
    """Return (status, parsed_or_raw_body). Never raises on an HTTP error status."""
    url = BASE_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body
    except urllib.error.URLError as exc:
        return 0, "network error: {}".format(exc.reason)


def list_models(key):
    status, body = call("/models", key)
    if status != 200:
        print("GET /models failed with status {}".format(status))
        print(json.dumps(body, indent=2) if isinstance(body, dict) else body)
        return []
    ids = sorted(m.get("id", "") for m in body.get("data", []))
    print("{} models visible to this account.".format(len(ids)))
    nemotron = [i for i in ids if "nemotron" in i.lower()]
    print("\nNemotron ids:")
    for i in nemotron or ["  (none)"]:
        print("  " + i)
    omni = [i for i in ids if "omni" in i.lower()]
    print("\nIds containing 'omni': {}".format(omni or "none"))
    return ids


def probe_once(key, model, verbose):
    left_name, right_name = random.sample(list(COLOURS), 2)
    png = png_two_halves(COLOURS[left_name], COLOURS[right_name])
    uri = "data:image/png;base64," + base64.b64encode(png).decode()

    question = (
        "This image is split into a left half and a right half, each one solid colour. "
        "Name them. Answer with exactly two words separated by a space, the left half "
        "first, each chosen from: red, green, blue, yellow. No other text."
    )
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 32,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url", "image_url": {"url": uri}},
                ],
            }
        ],
    }

    print("  drew: left={} right={}".format(left_name, right_name))
    status, body = call("/chat/completions", key, payload)

    if status != 200:
        print("  HTTP {} from /chat/completions".format(status))
        print("  verbatim error body:")
        print("  " + (json.dumps(body, indent=2).replace("\n", "\n  ") if isinstance(body, dict) else str(body)))
        return "ERROR", None

    answer = (body.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
    usage = body.get("usage", {})
    print("  model said: {!r}".format(answer))
    if verbose:
        print("  usage: " + json.dumps(usage))

    words = [w.strip(".,:;'\"").lower() for w in answer.replace("\n", " ").split()]
    hit = len(words) >= 2 and words[0] == left_name and words[1] == right_name
    print("  -> {}".format("correct" if hit else "wrong"))
    return ("PASS" if hit else "FAIL"), usage


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", help="force a model id instead of discovering one")
    ap.add_argument("--list", action="store_true", help="only list models, do not probe")
    ap.add_argument("--repeat", type=int, default=1, help="number of independent image trials")
    ap.add_argument("--verbose", action="store_true", help="print token usage per call")
    args = ap.parse_args()

    key = load_key()
    if not key:
        print("No API key found. Set one of {} in the environment or in .env.".format(", ".join(KEY_NAMES)))
        print("Claim credits first: code NEBIUS-DEVPOST-GLOBAL26 at https://nebius.com/promo-code")
        return 2

    print("Base URL: {}\n".format(BASE_URL))
    ids = list_models(key)
    if args.list:
        return 0

    model = args.model
    if not model:
        omni = [i for i in ids if "omni" in i.lower()]
        if not omni:
            print("\nNo model id contains 'omni'.")
            print("Gate 1 result: Nemotron 3 Nano Omni is NOT reachable on this account by that name.")
            print("Do not assume it is absent from Token Factory entirely. Ask in the Nebius Discord")
            print("(https://discord.gg/ZdC3rXMJH), and re-run with --model once you have the real id.")
            print("Fallback per 00-DAY-ONE-GATES.md: a Nemotron text model plus a separate NVIDIA open")
            print("vision model, and the sponsor-depth claim has to be re-argued.")
            return 1
        model = omni[0]
        print("\nDiscovered model: {}".format(model))

    print("\nProbing {} with {} trial(s).".format(model, args.repeat))
    print("A text-only model guessing has a 1 in 12 chance per trial.\n")

    results = []
    for n in range(args.repeat):
        print("trial {}:".format(n + 1))
        verdict, _ = probe_once(key, model, args.verbose)
        results.append(verdict)
        if verdict == "ERROR":
            break

    print("\n" + "=" * 60)
    if "ERROR" in results:
        print("GATE 1: ERROR. The request was rejected. Read the verbatim body above.")
        print("A 400 naming the content type means images are not accepted on this model.")
        return 1
    passes = results.count("PASS")
    print("GATE 1: {}/{} trials correct.".format(passes, len(results)))
    if passes == len(results):
        print("VERDICT: images are accepted and the model demonstrably saw them.")
        print("An NVIDIA model can do the perception. Proceed.")
        return 0
    if passes == 0:
        print("VERDICT: the call succeeded but the model never saw the image.")
        print("This is the dangerous outcome: it looks like it works and does not.")
        print("Treat the image path as unavailable and use the fallback.")
        return 1
    print("VERDICT: mixed. Not a clean gate. Re-run with a higher --repeat before trusting it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
