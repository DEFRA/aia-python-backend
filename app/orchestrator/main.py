import asyncio
import signal
import sys
from app.core.dependencies import get_document_repository, get_s3_service, get_sqs_service, get_ingestor_service
from app.utils.postgres import init_db, get_postgres_pool, close_postgres_pool
from app.utils.logger import get_logger
from app.core.config import config

logger = get_logger("app.worker")

class DocumentWorker:
    def __init__(self):
        self.running = True

    def stop(self, *args):
        logger.info("Shutdown signal received. Stopping worker...")
        self.running = False

    async def _cleanup_task(self, repo):
        while self.running:
            try:
                await repo.cleanup_stuck_documents(timeout_minutes=config.app.worker_stuck_task_timeout_minutes)
            except Exception as e:
                logger.exception("Error in cleanup task: %s", str(e))
            # Sleep for 5 minutes before checking again
            await asyncio.sleep(300)

    async def run(self):
        # Register signals for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stop)

        logger.info("Initializing worker components...")
        await init_db()
        pool = await get_postgres_pool()
        
        # Manual dependency injection for the standalone process
        from app.utils.app_context import AppContext
        context = AppContext()
        repo = get_document_repository(pool, context)
        s3 = get_s3_service()
        sqs = get_sqs_service()
        ingestor = get_ingestor_service(repo, s3, sqs)

        logger.info("Worker started. Polling for documents...")
        cleanup_bg_task = asyncio.create_task(self._cleanup_task(repo))

        while self.running:
            try:
                processed = await ingestor.process_batch(limit=5)
                if processed > 0:
                    logger.info("Successfully processed %d documents.", processed)
                    # Don't sleep if we found work; keep going
                    continue
                else:
                    # No work found, sleep for a bit
                    await asyncio.sleep(5)
            except Exception as e:
                logger.exception("Error in worker loop: %s", str(e))
                await asyncio.sleep(10) # Sleep longer on error

        logger.info("Closing database pool...")
        cleanup_bg_task.cancel()
        await close_postgres_pool()
        logger.info("Worker stopped.")

if __name__ == "__main__":
    worker = DocumentWorker()
    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.critical("Fatal worker error: %s", str(e))
        sys.exit(1)
