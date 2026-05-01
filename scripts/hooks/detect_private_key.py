#!/usr/bin/env python3
"""Block PEM private keys from being committed."""
import re, sys

PATTERN = re.compile(r"-----BEGIN\s+(?:\w+ )?PRIVATE KEY-----")
failed = False
for path in sys.argv[1:]:
    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
        if PATTERN.search(content):
            print(f"Private key detected: {path}")
            failed = True
    except OSError:
        pass
sys.exit(1 if failed else 0)
