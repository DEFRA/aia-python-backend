"""Pytest configuration for agents/evaluation tests.

Ensures repository-root imports (``app.*``) work regardless of pytest's
chosen working directory.
"""

import sys
from pathlib import Path

# tests/agents_evaluation/ -> tests/ -> repository root
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
