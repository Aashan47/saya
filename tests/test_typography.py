#!/usr/bin/env python3
"""No em dash may exist as a literal character anywhere in this project.

The rule covers code comments, READMEs, firmware and video captions, not just prose.
Test fixtures that need the character construct it with an escape sequence, so this
sweep has no exceptions to remember.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXTS = (".py", ".md", ".ino", ".h", ".txt", ".json", ".html", ".css", ".js")
EM_DASH = chr(0x2014)


def main():
    offenders = []
    scanned = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "data", ".git", "frames")]
        for name in files:
            if not name.endswith(EXTS):
                continue
            path = os.path.join(base, name)
            scanned += 1
            for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                if EM_DASH in line:
                    offenders.append("{}:{}".format(os.path.relpath(path, ROOT), n))

    print("scanned {} files under {}".format(scanned, ROOT))
    if offenders:
        print("\nFAIL: em dash found at:")
        for o in offenders:
            print("  " + o)
        return 1
    print("pass  no em dash anywhere")
    return 0


if __name__ == "__main__":
    sys.exit(main())
