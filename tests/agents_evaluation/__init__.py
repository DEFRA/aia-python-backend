"""Initialize tests/agents_evaluation package and set up import paths.

This module's sys.path setup is loaded by:
1. Pytest at collection time (via conftest discovery)
2. VS Code Pylance during IDE analysis (because __init__.py is loaded on import)

This ensures imports like `from src.agents.schemas` resolve correctly
in both runtime and IDE environments.
"""

import sys
from pathlib import Path

# tests/agents_evaluation/ -> tests/ -> repo root -> app/agents/evaluation
_agents_eval_root = (
    Path(__file__).resolve().parent.parent.parent / "app" / "agents" / "evaluation"
)
if str(_agents_eval_root) not in sys.path:
    sys.path.insert(0, str(_agents_eval_root))
