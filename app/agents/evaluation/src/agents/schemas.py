"""Pydantic schemas for security assessment agent I/O and EventBridge detail payloads."""

from typing import Literal

from pydantic import BaseModel


class AssessmentRow(BaseModel):
    """A single checklist question with its coverage rating and supporting evidence."""

    Question: str
    Coverage: str  # "Green", "Amber", or "Red"
    Evidence: str


class FinalSummary(BaseModel):
    """Overall summary produced by the LLM after assessing all questions."""

    Interpretation: str
    Overall_Comments: str


class LLMResponseMeta(BaseModel):
    """Metadata extracted from the raw Anthropic API response."""

    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None = None


class AgentResult(BaseModel):
    """Complete result returned by a security or compliance agent."""

    assessments: list[AssessmentRow]
    metadata: LLMResponseMeta
    final_summary: FinalSummary | None = None


class TaggedChunk(BaseModel):
    """A document chunk enriched with security/governance tags."""

    chunk_index: int
    page: int
    is_heading: bool
    text: str
    relevant: bool
    tags: list[str]
    reason: str | None


# ---------------------------------------------------------------------------
# EventBridge detail payload models — one per pipeline stage transition
# ---------------------------------------------------------------------------


class DocumentParsedDetail(BaseModel):
    """Detail payload for the ``DocumentParsed`` event (Stage 3 -> 4)."""

    docId: str
    chunksCacheKey: str
    contentHash: str


class DocumentTaggedDetail(BaseModel):
    """Detail payload for the ``DocumentTagged`` event (Stage 4 -> 5)."""

    docId: str
    taggedCacheKey: str
    contentHash: str


class SectionsReadyDetail(BaseModel):
    """Detail payload for the ``SectionsReady`` event (Stage 5 -> 6).

    Emitted once per agent type during the fan-out.
    """

    docId: str
    agentType: Literal["security", "data", "risk", "ea", "solution"]


class AgentCompleteDetail(BaseModel):
    """Detail payload for the ``AgentComplete`` event (Stage 6 -> 7)."""

    docId: str
    agentType: str


class AllAgentsCompleteDetail(BaseModel):
    """Detail payload for the ``AllAgentsComplete`` event (Stage 7 trigger)."""

    docId: str


class DocumentCompiledDetail(BaseModel):
    """Detail payload for the ``DocumentCompiled`` event (Stage 7 -> 8)."""

    docId: str
    compiledCacheKey: str


class ResultPersistedDetail(BaseModel):
    """Detail payload for the ``ResultPersisted`` event (Stage 8a -> 9)."""

    docId: str


class DocumentMovedDetail(BaseModel):
    """Detail payload for the ``DocumentMoved`` event (Stage 8b -> 9)."""

    docId: str
    destination: Literal["completed", "error"]


class FinaliseReadyDetail(BaseModel):
    """Detail payload for the ``FinaliseReady`` event (Stage 8 fan-in -> 9)."""

    docId: str


class PipelineCompleteDetail(BaseModel):
    """Detail payload for the ``PipelineComplete`` event (Stage 9 terminal)."""

    docId: str
    status: Literal["completed", "error"]
