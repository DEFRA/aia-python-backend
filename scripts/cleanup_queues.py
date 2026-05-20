#!/usr/bin/env python3
"""Purge AIA SQS queues (task, status, and optionally DLQs).

Usage:
    python scripts/cleanup_queues.py
    python scripts/cleanup_queues.py --yes
    python scripts/cleanup_queues.py --include-dlq
    python scripts/cleanup_queues.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiobotocore.session
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# ── Colours ───────────────────────────────────────────────────────────────────
_GREEN  = "\033[0;32m"
_YELLOW = "\033[1;33m"
_RED    = "\033[0;31m"
_BOLD   = "\033[1m"
_NC     = "\033[0m"


def ok(label: str, detail: str = "") -> None:
    print(f"{_GREEN}  ✓{_NC}  {label:<28} {detail}")


def fail(label: str, detail: str = "") -> None:
    print(f"{_RED}  ✗{_NC}  {label:<28} {detail}")


def warn(label: str, detail: str = "") -> None:
    print(f"{_YELLOW}  !{_NC}  {label:<28} {detail}")


def info(label: str, detail: str = "") -> None:
    print(f"     {label:<28} {detail}")


def banner(title: str) -> None:
    print(f"\n{_BOLD}{title}{_NC}")
    print("─" * 58)

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

sys.path.insert(0, str(_ROOT))

from app.config import config  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Purge task/status SQS queues and optionally their DLQs."
    )
    parser.add_argument(
        "--include-dlq",
        action="store_true",
        help="Also discover and purge DLQs referenced by the task/status queues.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be purged.",
    )
    return parser.parse_args()


@asynccontextmanager
async def _sqs_client():
    session = aiobotocore.session.get_session()
    kwargs: dict[str, Any] = {
        "service_name": "sqs",
        "region_name": config.aws.region,
        "aws_access_key_id": config.aws.access_key_id,
        "aws_secret_access_key": config.aws.secret_access_key,
    }
    if config.aws.session_token:
        kwargs["aws_session_token"] = config.aws.session_token
    if config.aws.endpoint_url:
        kwargs["endpoint_url"] = config.aws.endpoint_url

    async with session.create_client(**kwargs) as client:
        yield client


def _queue_name_from_arn(arn: str) -> str:
    return arn.split(":")[-1]


def _account_from_arn(arn: str) -> str:
    parts = arn.split(":")
    return parts[4] if len(parts) > 4 else ""


async def _get_dlq_url(client: Any, queue_url: str) -> str | None:
    response = await client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["RedrivePolicy"],
    )
    redrive = response.get("Attributes", {}).get("RedrivePolicy")
    if not redrive:
        return None

    try:
        policy = json.loads(redrive)
    except json.JSONDecodeError as exc:
        warn("RedrivePolicy", f"malformed for {queue_url}: {exc}")
        return None

    dlq_arn = policy.get("deadLetterTargetArn")
    if not dlq_arn:
        return None

    queue_name = _queue_name_from_arn(dlq_arn)
    account_id = _account_from_arn(dlq_arn)

    kwargs: dict[str, Any] = {"QueueName": queue_name}
    if account_id:
        kwargs["QueueOwnerAWSAccountId"] = account_id

    dlq_resp = await client.get_queue_url(**kwargs)
    return dlq_resp["QueueUrl"]


async def _queue_depth(client: Any, queue_url: str) -> tuple[str, str]:
    response = await client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        ],
    )
    attrs = response.get("Attributes", {})
    return (
        attrs.get("ApproximateNumberOfMessages", "0"),
        attrs.get("ApproximateNumberOfMessagesNotVisible", "0"),
    )


async def _purge_queue(client: Any, queue_url: str) -> None:
    """Purge a queue. Falls back to drain-by-delete if PurgeQueue is denied."""
    try:
        await client.purge_queue(QueueUrl=queue_url)
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        if code not in ("AccessDenied", "AccessDeniedException"):
            raise
        info("PurgeQueue denied", f"{queue_url} — falling back to drain-by-delete.")

    # Drain by receiving and deleting in batches of 10
    deleted_total = 0
    while True:
        resp = await client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=0,
            AttributeNames=["All"],
        )
        messages = resp.get("Messages", [])
        if not messages:
            break
        entries = [
            {"Id": str(i), "ReceiptHandle": m["ReceiptHandle"]}
            for i, m in enumerate(messages)
        ]
        await client.delete_message_batch(QueueUrl=queue_url, Entries=entries)
        deleted_total += len(messages)

    info("Drained", f"{deleted_total} message(s) from {queue_url}")


async def run(include_dlq: bool, auto_yes: bool, dry_run: bool) -> int:
    if not config.sqs.task_queue_url or not config.sqs.status_queue_url:
        fail("Config", "Missing TASK_QUEUE_URL or STATUS_QUEUE_URL in configuration.")
        return 1

    queue_urls: list[str] = [
        config.sqs.task_queue_url,
        config.sqs.status_queue_url,
    ]

    async with _sqs_client() as client:
        if include_dlq:
            discovered_dlqs: list[str] = []
            for q in queue_urls:
                try:
                    dlq = await _get_dlq_url(client, q)
                except Exception as exc:
                    warn("DLQ inspection", f"failed for {q}: {exc}")
                    continue
                if dlq:
                    discovered_dlqs.append(dlq)
            queue_urls.extend(discovered_dlqs)

        # Preserve order, remove duplicates
        unique_queues: list[str] = list(dict.fromkeys(queue_urls))

        banner("Queues selected for cleanup")
        for q in unique_queues:
            try:
                visible, inflight = await _queue_depth(client, q)
                info(q, f"visible={visible}, in-flight={inflight}")
            except Exception as exc:
                warn(q, f"depth unavailable: {exc}")

        if dry_run:
            info("Dry run complete", "No queues were purged.")
            return 0

        if not auto_yes:
            confirm = input("\nType 'yes' to purge all queues above: ").strip().lower()
            if confirm != "yes":
                warn("Aborted", "No queues were purged.")
                return 1

        failures = 0
        for q in unique_queues:
            try:
                await _purge_queue(client, q)
                ok("Cleanup completed", q)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "Unknown")
                if code == "PurgeQueueInProgress":
                    warn("Purge in progress", f"{q} (SQS allows one purge per 60 seconds).")
                else:
                    failures += 1
                    fail("Cleanup failed", f"{q}: {exc}")
            except Exception as exc:
                failures += 1
                fail("Cleanup failed", f"{q}: {exc}")

        if failures:
            fail("Completed", f"{failures} failure(s).")
            return 1

        ok("Queue cleanup completed successfully", "")
        return 0


if __name__ == "__main__":
    args = _parse_args()
    try:
        raise SystemExit(
            asyncio.run(
                run(
                    include_dlq=args.include_dlq,
                    auto_yes=args.yes,
                    dry_run=args.dry_run,
                )
            )
        )
    except KeyboardInterrupt:
        warn("Interrupted", "")
        raise SystemExit(130)
