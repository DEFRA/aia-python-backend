from enum import Enum

class UploadStatus(str, Enum):
    ANALYSING = "Analysing"
    SUCCESS = "Success"
    FAILED = "Failed"

class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
