import sys
from pathlib import Path

# Add core_backend src to path so that relative imports (utils, models, services, etc.) 
# resolve to app/core_backend/src namespace
_CORE_SRC = Path(__file__).resolve().parents[3] / "app" / "core_backend" / "src"
sys.path.insert(0, str(_CORE_SRC))
