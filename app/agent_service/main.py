"""Backward-compatible re-export — actual implementation in src/main.py."""

from app.agent_service.src.main import app, main  # noqa: F401

if __name__ == "__main__":
    main()
