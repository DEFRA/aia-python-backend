import json
import logging

from app.datapipeline.src.main import run

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """AWS Lambda entry point.

    Invoked by EventBridge Scheduler on the configured schedule.
    Delegates entirely to main.run() and returns a structured response.
    """
    try:
        summary = run()
        logger.info("Pipeline complete: %s", summary)
        return {"statusCode": 200, "body": json.dumps(summary)}
    except Exception as exc:
        logger.critical("Pipeline failed: %s", exc, exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
