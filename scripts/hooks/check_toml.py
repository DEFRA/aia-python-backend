#!/usr/bin/env python3
"""Validate TOML syntax."""
import sys
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        print("Neither tomllib nor tomli available — skipping TOML check")
        sys.exit(0)

failed = False
for path in sys.argv[1:]:
    try:
        tomllib.loads(open(path, encoding="utf-8").read())
    except Exception as exc:
        print(f"TOML error in {path}: {exc}")
        failed = True
sys.exit(1 if failed else 0)
