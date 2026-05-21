from pydantic import BaseModel


class OrchestrateRequest(BaseModel):
    """Payload sent by CoreBackend to the Orchestrator's POST /orchestrate endpoint."""

    document_id: str
    s3_key: str
    template_type: str
