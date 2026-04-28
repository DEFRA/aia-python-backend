import logging
import sys

# Default log format: [Timestamp] [LEVEL] LoggerName: Message
DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

def get_logger(name: str) -> logging.Logger:

    if not logging.getLogger().hasHandlers():
        # Late import to avoid circular dependency with app.config
        try:
            from app.core.config import config
            level_name = config.app.log_level.value.upper()
        except (ImportError, AttributeError):
            level_name = "INFO"
            
        level = getattr(logging, level_name, logging.INFO)

        logging.basicConfig(
            level=level,
            format=DEFAULT_FORMAT,
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        
    return logging.getLogger(name)
