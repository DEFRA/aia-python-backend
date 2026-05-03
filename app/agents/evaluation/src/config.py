"""Configuration classes for the evaluation pipeline.

Operational defaults (models, pipeline constants) are loaded from
``app/agents/evaluation/config.yaml``.  Secrets and deployment-specific values
(API keys, DB credentials) are sourced from environment variables only.
Precedence: environment variables > yaml values > code defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import Field, computed_field
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
)

# ---------------------------------------------------------------------------
# YAML loader and settings source
# ---------------------------------------------------------------------------


_YAML_CACHE: dict[str, Any] | None = None


def _load_yaml() -> dict[str, Any]:
    """Load ``config.yaml`` from ``app/agents/evaluation/`` once per cold start.

    Returns:
        The parsed YAML as a dict.  If the file is missing or empty, returns
        an empty dict so that env-only fields still validate correctly.
    """
    global _YAML_CACHE  # noqa: PLW0603
    if _YAML_CACHE is not None:
        return _YAML_CACHE

    yaml_path: Path = Path(__file__).resolve().parent.parent / "config.yaml"
    try:
        raw: str = yaml_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _YAML_CACHE = {}
        return _YAML_CACHE

    parsed: Any = yaml.safe_load(raw) or {}
    _YAML_CACHE = parsed if isinstance(parsed, dict) else {}
    return _YAML_CACHE


def _select_nested(data: dict[str, Any], dotted_key: str) -> dict[str, Any]:
    """Return the nested dict located at ``dotted_key`` or an empty dict."""
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return {}
        current = current[part]
    if not isinstance(current, dict):
        return {}
    return current


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Pydantic settings source backed by a section of ``config.yaml``.

    Accepts a dotted ``yaml_key`` (e.g. ``"agents.security"``) and returns the
    matching dict.  Precedence relative to env vars is controlled by the order
    returned from ``settings_customise_sources``.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        yaml_key: str,
    ) -> None:
        """Initialise the YAML source.

        Args:
            settings_cls: The ``BaseSettings`` subclass this source populates.
            yaml_key: Dotted path to the YAML section (e.g. ``"cache"``).
        """
        super().__init__(settings_cls)
        self._yaml_key: str = yaml_key

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        """Return the value of a single field from the YAML section."""
        section: dict[str, Any] = _select_nested(_load_yaml(), self._yaml_key)
        if field_name in section:
            return section[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        """Return the full YAML section as a flat dict for model population."""
        return _select_nested(_load_yaml(), self._yaml_key)


# ---------------------------------------------------------------------------
# Shared BaseSettings helper — sets source precedence
# ---------------------------------------------------------------------------


def _make_customise_sources(yaml_key: str) -> classmethod[Any, Any, Any]:
    """Build a ``settings_customise_sources`` classmethod bound to ``yaml_key``.

    Precedence is: init kwargs > env vars > .env file > yaml > file secrets.
    """

    def settings_customise_sources(  # noqa: PLR0913
        cls: type[BaseSettings],
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlSettingsSource(settings_cls, yaml_key=yaml_key),
            file_secret_settings,
        )

    return classmethod(settings_customise_sources)


# ---------------------------------------------------------------------------
# LLM client config
# ---------------------------------------------------------------------------


class LLMConfig(BaseSettings):
    """LLM provider selection.

    ``provider`` controls which Anthropic client the pipeline constructs:

    * ``"anthropic"`` — direct Anthropic API; requires ``ANTHROPIC_API_KEY``.
    * ``"bedrock"``   — AWS Bedrock; uses the boto3 credential chain, no API key.

    Override via the ``LLM_PROVIDER`` environment variable or the ``llm.provider``
    key in ``config.yaml``.
    """

    provider: Literal["anthropic", "bedrock"] = Field(default="anthropic", alias="LLM_PROVIDER")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("llm")


# ---------------------------------------------------------------------------
# Agent configs
# ---------------------------------------------------------------------------


class SecurityAgentConfig(BaseSettings):
    """Configuration for the SecurityAgent."""

    model: str = Field(alias="SECURITY_MODEL")
    max_tokens: int = Field(default=4096, alias="SECURITY_MAX_TOKENS")
    temperature: float = Field(default=0.0, alias="SECURITY_TEMPERATURE")
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("agents.security")


class TechnicalAgentConfig(BaseSettings):
    """Configuration for the TechnicalAgent.

    Drives the technical compliance assessment (DPA 2018, UK GDPR,
    public-sector records management). Mirrors ``SecurityAgentConfig``
    so the two agents are interchangeable from the registry's perspective.
    """

    model: str = Field(alias="TECHNICAL_MODEL")
    max_tokens: int = Field(default=4096, alias="TECHNICAL_MAX_TOKENS")
    temperature: float = Field(default=0.0, alias="TECHNICAL_TEMPERATURE")
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("agents.technical")


class TaggingAgentConfig(BaseSettings):
    """Configuration for the TaggingAgent (Stage 4)."""

    model: str = Field(alias="TAGGING_MODEL")
    batch_size: int = Field(default=15, alias="TAGGING_BATCH_SIZE")
    max_tokens: int = Field(default=4096, alias="TAGGING_MAX_TOKENS")
    temperature: float = Field(default=0.0, alias="TAGGING_TEMPERATURE")
    api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("agents.tagging")


# ---------------------------------------------------------------------------
# CloudWatch config
# ---------------------------------------------------------------------------


class CloudWatchConfig(BaseSettings):
    """CloudWatch metric publishing configuration."""

    namespace: str = Field(default="Defra/Pipeline", alias="CLOUDWATCH_NAMESPACE")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("cloudwatch")


# ---------------------------------------------------------------------------
# Pipeline constants
# ---------------------------------------------------------------------------


class PipelineConfig(BaseSettings):
    """Pipeline-wide constants (agent types, SQS limits, tag routing)."""

    agent_types: list[str] = Field(
        default_factory=lambda: ["security", "technical"],
        alias="PIPELINE_AGENT_TYPES",
    )
    section_labels: dict[str, str] = Field(
        default_factory=lambda: {"security": "Security Policy", "technical": "Technology Policy"},
        alias="PIPELINE_SECTION_LABELS",
    )
    sqs_inline_limit: int = Field(default=240_000, alias="PIPELINE_SQS_INLINE_LIMIT")
    agent_tag_map: dict[str, list[str]] = Field(
        default_factory=dict,
        alias="PIPELINE_AGENT_TAG_MAP",
    )

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("pipeline")


# ---------------------------------------------------------------------------
# Document parser config
# ---------------------------------------------------------------------------


class ParserConfig(BaseSettings):
    """Document parser tunables."""

    min_text_chars: int = Field(default=100, alias="PARSER_MIN_TEXT_CHARS")
    chunk_max_chars: int = Field(default=1_500, alias="PARSER_CHUNK_MAX_CHARS")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("parser")


# ---------------------------------------------------------------------------
# Local runner config (main.py)
# ---------------------------------------------------------------------------


class LocalRunnerConfig(BaseSettings):
    """Defaults for the local end-to-end runner (``main.py``).

    Drives the mock-SQS pipeline runner: which folder to read the input
    document and assessment file from, which document to use by default,
    where to write the combined output JSON, and how to map agent type
    identifiers to the section keys that appear in the output.
    """

    data_dir: str = Field(default="data", alias="LOCAL_RUNNER_DATA_DIR")
    assessment_filename: str = Field(
        default="sample_policy_assessment.json",
        alias="LOCAL_RUNNER_ASSESSMENT_FILENAME",
    )
    default_input_filename: str = Field(
        default="fictional_product_logistics_report.pdf",
        alias="LOCAL_RUNNER_DEFAULT_INPUT_FILENAME",
    )
    output_filename_template: str = Field(
        default="pipeline_output_{doc_id}.json",
        alias="LOCAL_RUNNER_OUTPUT_FILENAME_TEMPLATE",
    )
    display_keys: dict[str, str] = Field(
        default_factory=lambda: {"security": "Security", "technical": "Technical"},
        alias="LOCAL_RUNNER_DISPLAY_KEYS",
    )

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("local_runner")


# ---------------------------------------------------------------------------
# Infrastructure configs
# ---------------------------------------------------------------------------


class EventBridgeConfig(BaseSettings):
    """Configuration for the EventBridge custom event bus."""

    bus_name: str = Field(default="defra-pipeline", alias="EVENTBRIDGE_BUS_NAME")
    source: str = Field(default="defra.pipeline", alias="EVENTBRIDGE_SOURCE")
    region: str = Field(default="eu-west-2", alias="AWS_REGION")

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("eventbridge")


class DatabaseConfig(BaseSettings):
    """Connection settings for the PostgreSQL questions database.

    ``db_host``, ``db_name``, ``db_user`` and ``db_password`` are env-only.
    ``db_port`` has a yaml-provided default.
    """

    db_host: str = Field(alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(alias="DB_NAME")
    db_user: str = Field(alias="DB_USER")
    db_password: str = Field(alias="DB_PASSWORD")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> str:
        """Build an asyncpg-compatible connection string from individual fields."""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    model_config = {"populate_by_name": True, "extra": "ignore"}
    settings_customise_sources = _make_customise_sources("database")
