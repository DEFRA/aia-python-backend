#!/usr/bin/env python3
"""Block files containing leftover merge conflict markers."""
import sys

MARKERS = ("<" * 7 + " ", ">" * 7 + " ", "=" * 7 + "\n")
failed = False
for path in sys.argv[1:]:
    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
        for marker in MARKERS:
            if marker in content:
                print(f"Merge conflict marker found: {path}")
                failed = True
                break
    except OSError:
        pass
sys.exit(1 if failed else 0)
