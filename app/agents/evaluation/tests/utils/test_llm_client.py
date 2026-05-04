"""Tests for src.utils.llm_client.make_llm_client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    import os

    import src.utils.llm_client as llm_module

    with (
        patch.dict(os.environ, {"LLM_PROVIDER": "bedrock"}),
        patch("src.utils.llm_client.anthropic.AsyncAnthropicBedrock") as mock_bedrock,
    ):
        mock_bedrock.return_value = MagicMock()
        # Reload to bust the module-level import; call directly
        llm_module.make_llm_client()

    mock_bedrock.assert_called_once_with()


def test_default_provider_is_bedrock() -> None:
    """With no env override, the default provider reads from config.yaml."""
    import src.config as cfg_mod
    from src.config import LLMConfig

    original_cache = cfg_mod._YAML_CACHE
    cfg_mod._YAML_CACHE = {"llm": {"provider": "bedrock"}}

    try:
        config = LLMConfig()
        assert config.provider == "bedrock"
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

    with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}), pytest.raises(ValidationError):
        LLMConfig()
