"""Pydantic schemas for security assessment agent I/O and EventBridge detail payloads."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Reference(BaseModel):
    """Authoritative reference for a question, echoed verbatim from the input JSON.

    ``text`` carries the per-question reference (e.g. ``"C1.a"``); ``url`` carries
    the category-level reference URL. Agents must echo these values back as-is and
    must not invent or rewrite them.
    """

    text: str
    url: str | None = None


class QuestionItem(BaseModel):
    """A checklist question paired with its authoritative reference.

    Sourced from the input assessment JSON (see
    ``app/agents/evaluation/files/system_input_output.md``). Carried through
    Stage 5 -> Stage 6 in the SQS Tasks message body so each agent can echo
    ``reference`` back into its output ``Reference`` field.
    """

    question: str
    reference: str


class AssessmentRow(BaseModel):
    """A single checklist question with its rating and supporting comments."""

    Question: str
    Rating: Literal["Green", "Amber", "Red"]
    Comments: str
    Reference: Reference


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
    agentType: Literal["security", "governance"]


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


# ---------------------------------------------------------------------------
# Stage 6 -> Stage 7 status message and Stage 7 compiled output
# ---------------------------------------------------------------------------


class AgentStatusMessage(BaseModel):
    """Status message published by Stage 6 agents to the SQS Status queue.

    Consumed by the Stage 7 compile handler.  The ``result`` field is a
    validated ``AgentResult`` on success, or ``None`` on failure.
    """

    docId: str
    agentType: str
    status: Literal["completed", "failed"]
    result: AgentResult | None
    durationMs: float
    completedAt: str
    errorMessage: str | None = None


class CompiledContentBlock(BaseModel):
    """A single content block inside a ``CompiledResult``.

    Mirrors the front-end contract where each block is a typed chunk of
    rendered output (currently only markdown text).
    """

    type: Literal["text"]
    text: str


class CompiledResult(BaseModel):
    """Compiled report payload produced by the Stage 7 compile handler.

    Shape matches the front-end response contract -- a list of typed content
    blocks plus scorecard and per-agent tables rendered into the first block.
    """

    docId: str
    type: str
    generatedAt: datetime
    content: list[CompiledContentBlock]
    status: Literal["completed", "error"]
    processedAt: datetime
