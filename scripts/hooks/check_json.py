#!/usr/bin/env python3
"""Validate JSON syntax."""
import sys, json

failed = False
for path in sys.argv[1:]:
    try:
        json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"JSON error in {path}: {exc}")
        failed = True
sys.exit(1 if failed else 0)
