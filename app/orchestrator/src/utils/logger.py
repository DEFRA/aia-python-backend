import logging
import os
import sys
from datetime import datetime, timezone

from pythonjsonlogger import jsonlogger

# Environment-configurable fields
SERVICE_NAME = os.getenv("SERVICE_NAME", "AIA Orchestrator")
EVENT_DATASET = os.getenv("EVENT_DATASET", "orchestrator.logs")


class ECSFormatter(jsonlogger.JsonFormatter):
    """ECS-compliant JSON log formatter."""

    def add_fields(self, log_record, record, message_dict):
        try:
            super().add_fields(log_record, record, message_dict)
        except Exception:
            # Ensure logging never fails due to formatting errors
            pass

        try:
            # ECS standard fields — all with safe defaults
            log_record["@timestamp"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            log_record["@timestamp"] = None

        try:
            log_record["log.level"] = (record.levelname or "INFO").lower()
        except Exception:
            log_record["log.level"] = "info"

        try:
            log_record["process.pid"] = os.getpid()
        except Exception:
            log_record["process.pid"] = None

        try:
            log_record["service.name"] = SERVICE_NAME
        except Exception:
            pass

        try:
            log_record["event.dataset"] = EVENT_DATASET
        except Exception:
            pass


def get_logger(name: str) -> logging.Logger:
    root_logger = logging.getLogger()
    if not root_logger.hasHandlers():
        try:
            try:
                from ..config.config import config

                level_name = config.log_level.value.upper()
            except (ImportError, AttributeError):
                level_name = "INFO"

            level = getattr(logging, level_name, logging.INFO)
            handler = logging.StreamHandler(sys.stdout)
            formatter = ECSFormatter(
                fmt="%(message)s",
                timestamp=True,
            )
            handler.setFormatter(formatter)
            root_logger.setLevel(level)
            root_logger.addHandler(handler)
        except Exception:
            # Fallback: ensure basic logging always works
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                handlers=[logging.StreamHandler(sys.stdout)],
            )

    return logging.getLogger(name)
