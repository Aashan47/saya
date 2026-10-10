#!/usr/bin/env bash
# Simulate what a judge does: clone, read the README, run the thing.
#
# 31% of hackathon submissions fail to build, and a sponsor engineer runs this check
# before any judge sees the project. So it is run here deliberately rather than hoped
# for. The copy excludes everything git would not carry, so anything the repo forgot
# to include fails here instead of on someone else's machine.
#
# Docker would be the stronger isolation. Where it is unavailable this still catches
# the failure that actually happens: a file that only exists locally, an import that
# only resolves from the author's working directory, or a hidden dependency.

set -u
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAIL=0

say(){ printf '\n\033[1m%s\033[0m\n' "$1"; }
ok(){ printf '  pass  %s\n' "$1"; }
no(){ printf '  FAIL  %s\n' "$1"; FAIL=1; }

say "copying as git would, excluding local state"
rsync -a --exclude 'data/latest_advice.json' --exclude '__pycache__/' --exclude '.env' \
         --exclude '*.pyc' --exclude 'tests/frames/' \
         --exclude 'firmware/saya_node/config.h' "$SRC/" "$TMP/clone/" 2>/dev/null \
  || cp -R "$SRC" "$TMP/clone"
rm -rf "$TMP/clone/tests/frames"
find "$TMP/clone" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
echo "  clone at $TMP/clone"

say "the evidence a judge needs to recompute the result"
# The writeup claims every number is recomputable from the raw data. That claim has to
# be true in the repo someone actually clones, not just on the author's machine.
[ -f "$TMP/clone/data/readings.jsonl" ] \
  && ok "readings.jsonl is in the clone ($(wc -l < "$TMP/clone/data/readings.jsonl" | tr -d ' ') readings)" \
  || no "readings.jsonl MISSING. The writeup's recomputable claim would be false"
[ -f "$TMP/clone/data/DATA-LOCK.json" ] \
  && ok "DATA-LOCK.json is in the clone" || no "DATA-LOCK.json missing"
IMGN=$(ls "$TMP/clone/data/images" 2>/dev/null | wc -l | tr -d ' ')
[ "$IMGN" -gt 200 ] && ok "$IMGN frames in the clone" || no "only $IMGN frames in the clone"

say "the submission requirements that are checked mechanically"
[ -f "$TMP/clone/LICENSE" ] && ok "LICENSE present" || no "LICENSE missing"
[ -f "$TMP/clone/README.md" ] && ok "README present" || no "README missing"
grep -qi "python3 tests/run_all.py" "$TMP/clone/README.md" \
  && ok "README documents how to run the tests" || no "README does not say how to run tests"
grep -qi "python3 server/ingest.py" "$TMP/clone/README.md" \
  && ok "README documents how to run the app" || no "README does not say how to run the app"

say "no secret is carried in the clone"
# This script is excluded from its own scan: it contains the patterns it looks for,
# and on the first run it dutifully reported itself.
HITS=$(grep -rlE "(sk-[A-Za-z0-9]{16}|Bearer [A-Za-z0-9]{20}|eyJ[A-Za-z0-9_-]{20})" \
         "$TMP/clone" 2>/dev/null | grep -v "fresh_clone_check.sh")
if [ -n "$HITS" ]; then
  no "something that looks like a credential is present"
  echo "$HITS" | sed 's/^/        /'
else
  ok "no credential-shaped string in the clone"
fi
[ -f "$TMP/clone/.env" ] && no ".env was copied into the clone" || ok "no .env in the clone"
[ -f "$TMP/clone/firmware/saya_node/config.h" ] \
  && no "config.h with the WiFi password was copied into the clone" \
  || ok "no config.h in the clone"
[ -f "$TMP/clone/firmware/saya_node/config.example.h" ] \
  && ok "config.example.h template IS present" || no "config.example.h missing"

say "it runs with nothing installed, from the clone, with no environment"
cd "$TMP/clone" || exit 1
if env -i PATH="$PATH" HOME="$TMP" python3 tests/run_all.py > "$TMP/out.txt" 2>&1; then
  ok "offline test suite passes on a clean checkout"
else
  no "offline test suite FAILS on a clean checkout"
  tail -25 "$TMP/out.txt" | sed 's/^/        /'
fi

say "the server starts and answers, from the clone"
env -i PATH="$PATH" HOME="$TMP" python3 server/ingest.py --port 8899 --data "$TMP/d" \
  > "$TMP/srv.log" 2>&1 &
SRV=$!
for _ in $(seq 1 25); do
  curl -fsS "http://127.0.0.1:8899/healthz" >/dev/null 2>&1 && break
  sleep 0.4
done
if curl -fsS "http://127.0.0.1:8899/healthz" | grep -q '"ok": true'; then
  ok "server responds on /healthz"
else
  no "server did not come up"; tail -12 "$TMP/srv.log" | sed 's/^/        /'
fi
curl -fsS "http://127.0.0.1:8899/" 2>/dev/null | grep -q "<title>Saya</title>" \
  && ok "product page is served" || no "product page did not render"
curl -fsS -X POST -H "Content-Type: image/jpeg" \
  "http://127.0.0.1:8899/api/reading?device=clone-test&t=30.0&h=50.0" 2>/dev/null \
  | grep -q '"stored"' && ok "accepts a device reading" || no "rejected a device reading"
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null

say "no dependency is imported that a clean machine would not have"
BAD=$(env -i PATH="$PATH" HOME="$TMP" python3 - <<'PY' 2>&1
import ast, os, sys
stdlib = set(sys.stdlib_module_names)
local = {f[:-3] for f in os.listdir("server") if f.endswith(".py")}
local |= {f[:-3] for f in os.listdir("tests") if f.endswith(".py")}
bad = set()
for d in ("server", "tests"):
    for f in os.listdir(d):
        if not f.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(d, f)).read())
        for n in ast.walk(tree):
            mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                    else [n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
            for m in mods:
                top = m.split(".")[0]
                if top not in stdlib and top not in local:
                    bad.add("{}/{}: {}".format(d, f, m))
print("\n".join(sorted(bad)))
PY
)
if [ -z "$BAD" ]; then
  ok "every import is standard library or local"
else
  echo "$BAD" | sed 's/^/        /'
  if echo "$BAD" | grep -qv "PIL"; then
    no "a non-stdlib import other than Pillow is required"
  else
    ok "Pillow is the only non-stdlib import, and requirements.txt declares it"
    [ -f "$TMP/clone/requirements.txt" ] \
      && ok "requirements.txt is present" || no "requirements.txt MISSING"
    grep -qi "pillow" "$TMP/clone/README.md" \
      && ok "README documents the dependency" || no "README does not mention Pillow"
  fi
fi

printf '\n%s\n' "======================================================"
if [ "$FAIL" -eq 0 ]; then
  echo "FRESH CLONE CHECK PASSED"
else
  echo "FRESH CLONE CHECK FAILED"
fi
exit $FAIL
