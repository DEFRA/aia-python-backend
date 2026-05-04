#!/usr/bin/env python3
"""Block files larger than 500 KB from being committed."""
import os, sys

MAX_KB = 500
failed = False
for path in sys.argv[1:]:
    if os.path.exists(path):
        size_kb = os.path.getsize(path) // 1024
        if size_kb > MAX_KB:
            print(f"Large file ({size_kb} KB > {MAX_KB} KB limit): {path}")
            failed = True
sys.exit(1 if failed else 0)
