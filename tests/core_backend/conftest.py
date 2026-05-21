import sys
from pathlib import Path

# Add both repo root and core_backend/src to path
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_BACKEND_SRC = _REPO_ROOT / "app" / "core_backend" / "src"

for path in [_REPO_ROOT, _CORE_BACKEND_SRC]:
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
