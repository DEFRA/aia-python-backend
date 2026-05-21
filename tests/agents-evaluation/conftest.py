"""Pytest configuration for agents/evaluation tests.

Sets up sys.path for tests at root level.
When tests run from root (pytest tests/agents-evaluation/), we need to ensure
that imports like `from src.agents.technical_agent` resolve to app/agents/evaluation/src/.
"""

import sys
from pathlib import Path

# tests/agents-evaluation/ -> tests/ -> root
# Then navigate to app/agents/evaluation
_agents_eval_root = Path(__file__).resolve().parent.parent.parent / "app" / "agents" / "evaluation"
if str(_agents_eval_root) not in sys.path:
    sys.path.insert(0, str(_agents_eval_root))
