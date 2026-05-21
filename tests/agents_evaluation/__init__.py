"""Initialize tests/agents_evaluation package and set up import paths.

This keeps imports stable in both pytest and IDE analysis contexts.
"""

import sys
from pathlib import Path

# tests/agents_evaluation/ -> tests/ -> repository root
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

_agents_eval_root = _repo_root / "app" / "agents" / "evaluation"
if str(_agents_eval_root) not in sys.path:
    sys.path.insert(0, str(_agents_eval_root))
