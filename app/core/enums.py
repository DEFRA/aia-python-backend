from enum import Enum


class DocumentStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETE = "COMPLETE"
    PARTIAL_COMPLETE = "PARTIAL_COMPLETE"
    ERROR = "ERROR"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
