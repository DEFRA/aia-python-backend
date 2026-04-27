import os
from contextlib import asynccontextmanager

from app.utils.logger import get_logger
import uvicorn
from fastapi import FastAPI

from app.utils.postgres import close_postgres_pool, init_db
# from app.utils.tracing import TraceIdMiddleware
from app.core.config import config
from app.api.health import router as health_router
from app.api.upload import router as upload_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Startup
    if config.db.uri:
        await init_db()
        logger.info("PostgreSQL initialised")
    yield
    # Shutdown
    await close_postgres_pool()


app = FastAPI(lifespan=lifespan)

# Setup middleware
# app.add_middleware(TraceIdMiddleware)

# Setup Routes
app.include_router(health_router)
app.include_router(upload_router)


def main() -> None:  # pragma: no cover
    if config.app.http_proxy:
        os.environ["HTTP_PROXY"] = str(config.app.http_proxy)
        os.environ["HTTPS_PROXY"] = str(config.app.http_proxy)
    else:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("HTTPS_PROXY", None)

    server_kwargs = {
        "app": "app.main:app",
        "host": config.app.host,
        "port": config.app.port,
        "reload": config.app.env == "development",
    }
    if config.app.log_config:
        server_kwargs["log_config"] = config.app.log_config

    uvicorn.run(**server_kwargs)


if __name__ == "__main__":  # pragma: no cover
    main()
