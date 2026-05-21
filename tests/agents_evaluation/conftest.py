"""Pytest configuration for agents/evaluation tests.

Ensures both repository-root imports (``app.*``) and local evaluation imports
work regardless of pytest's chosen working directory.
"""

import sys
from pathlib import Path

# tests/agents_evaluation/ -> tests/ -> repository root
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Keep direct access to the evaluation package path for any local-only imports.
_agents_eval_root = _repo_root / "app" / "agents" / "evaluation"
if str(_agents_eval_root) not in sys.path:
    sys.path.insert(0, str(_agents_eval_root))
