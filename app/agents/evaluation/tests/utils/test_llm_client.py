"""Tests for src.utils.llm_client.make_llm_client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------


def test_anthropic_provider_returns_async_anthropic() -> None:
    """make_llm_client() returns AsyncAnthropic when provider is 'anthropic'."""
    from src.utils.llm_client import make_llm_client

    with patch("src.utils.llm_client.LLMConfig") as mock_cfg_cls:
        mock_cfg_cls.return_value.provider = "anthropic"
        with patch("src.utils.llm_client.anthropic.AsyncAnthropic") as mock_cls:
            client = make_llm_client()

    mock_cls.assert_called_once_with()
    assert client is mock_cls.return_value


def test_bedrock_provider_returns_async_anthropic_bedrock() -> None:
    """make_llm_client() returns AsyncAnthropicBedrock when provider is 'bedrock'."""
    from src.utils.llm_client import make_llm_client

    with patch("src.utils.llm_client.LLMConfig") as mock_cfg_cls:
        mock_cfg_cls.return_value.provider = "bedrock"
        with patch("src.utils.llm_client.anthropic.AsyncAnthropicBedrock") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = make_llm_client()

    mock_cls.assert_called_once_with()
    assert client is mock_cls.return_value


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


def test_provider_read_from_env_var() -> None:
    """LLM_PROVIDER env var overrides config.yaml value."""
    import importlib
    import os

    import src.utils.llm_client as llm_module

    with (
        patch.dict(os.environ, {"LLM_PROVIDER": "bedrock"}),
        patch("src.utils.llm_client.anthropic.AsyncAnthropicBedrock") as mock_bedrock,
    ):
        mock_bedrock.return_value = MagicMock()
        # Reload to bust the module-level import; call directly
        client = llm_module.make_llm_client()

    mock_bedrock.assert_called_once_with()


def test_default_provider_is_anthropic() -> None:
    """With no env override, the default provider is 'anthropic'."""
    from src.config import LLMConfig

    # Clear any cached YAML to get a clean read
    import src.config as cfg_mod

    original_cache = cfg_mod._YAML_CACHE
    cfg_mod._YAML_CACHE = None

    try:
        config = LLMConfig()
        assert config.provider == "anthropic"
    finally:
        cfg_mod._YAML_CACHE = original_cache


# ---------------------------------------------------------------------------
# Invalid provider rejected at config level
# ---------------------------------------------------------------------------


def test_invalid_provider_raises() -> None:
    """An unrecognised LLM_PROVIDER value is rejected by Pydantic validation."""
    import os

    from pydantic import ValidationError

    from src.config import LLMConfig

    with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}):
        with pytest.raises(ValidationError):
            LLMConfig()
