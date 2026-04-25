import asyncio
from app.orchestrator.main import DocumentWorker

# This is a shim to support legacy app.worker commands
if __name__ == "__main__":
    worker = DocumentWorker()
    asyncio.run(worker.run())
