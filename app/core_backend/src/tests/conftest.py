import sys
from pathlib import Path

# Put the core service's `src/` (which holds the `app.*` namespace) ahead of
# the project root so `app.api.main`, `app.services.upload_service`, etc.
# resolve to the core service's own copies rather than picking up partial
# matches from the trimmed-down project-root `app/`.
_CORE_SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_CORE_SRC))
