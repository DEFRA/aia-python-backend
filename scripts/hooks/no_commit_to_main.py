#!/usr/bin/env python3
"""Prevent direct commits to the main branch."""
import subprocess, sys

result = subprocess.run(
    ["git", "symbolic-ref", "HEAD"],
    capture_output=True, text=True,
)
branch = result.stdout.strip()
if branch == "refs/heads/main":
    print("Direct commits to main are not allowed. Use a feature branch.")
    sys.exit(1)
