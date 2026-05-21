import sys
from pathlib import Path

# Ensure the repo root is on sys.path so root-level app imports (services, repositories, etc.) resolve.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
