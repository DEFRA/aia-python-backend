"""Pytest configuration for agents/evaluation tests.

Sets up sys.path so that relative imports like `from src.agents.technical_agent` work
when pytest runs from the app/agents/evaluation directory (as in CI with working-directory).
"""

import sys
from pathlib import Path

# When pytest runs with working-directory: app/agents/evaluation,
# it sets rootdir to that directory. Add the parent directory to sys.path
# so imports like `from src.agents.technical_agent` resolve correctly.
_agents_eval_root = Path(__file__).resolve().parent.parent
if str(_agents_eval_root) not in sys.path:
    sys.path.insert(0, str(_agents_eval_root))
