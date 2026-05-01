#!/usr/bin/env python3
"""Validate YAML syntax."""
import sys, yaml

failed = False
for path in sys.argv[1:]:
    try:
        yaml.safe_load(open(path, encoding="utf-8").read())
    except yaml.YAMLError as exc:
        print(f"YAML error in {path}: {exc}")
        failed = True
sys.exit(1 if failed else 0)
