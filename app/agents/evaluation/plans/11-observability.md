# Plan 11 — Observability (CloudWatch)

**Priority:** 11 (configure after infrastructure is deployed)

**Depends on:** Plan 10 (all Lambda functions deployed)

---

## Goal

Configure the CloudWatch observability layer described in `aws_event_driven_orchestration.md`:

- Per-stage error rate alarms
- DLQ depth alarm
- `AgentDuration` custom metric alarm (slowest agent detection)
- Queue backlog alarm
- Redis memory pressure alarm
- Pipeline health dashboard aggregating all of the above
- End-to-end duration metric derived from `PipelineStart` + `PipelineComplete` events

---

## Metric strategy

### Built-in Lambda metrics (no code changes needed)

| Metric | Alarm threshold |
|--------|----------------|
| `AWS/Lambda Errors` per function | > 5 errors in 5 min |
| `AWS/Lambda Duration` per function | p99 > function timeout × 0.8 |
| `AWS/Lambda Throttles` per function | > 0 throttles in 5 min |

### Custom metrics (emitted by Lambda code — see Plans 03–09)

| Metric | Emitted by | Dimension |
|--------|-----------|-----------|
| `ParseDuration` | Stage 3 | — |
| `TaggingDuration` | Stage 4 | — |
| `TaggedChunkCount` | Stage 4 | — |
| `SectionCount` | Stage 5 | `agentType` |
| `AgentDuration` | Stage 6 | `agentType` |
| `AgentSuccess` | Stage 6 | `agentType` |
| `AgentFailure` | Stage 6 | `agentType` |

All custom metrics go in the `DefraPipeline` namespace.

### SQS metrics

| Metric | Alarm threshold |
|--------|----------------|
| `ApproximateNumberOfMessagesVisible` (main queue) | > 50 messages — backlog warning |
| `ApproximateNumberOfMessagesVisible` (DLQ) | > 0 — page on-call immediately |

### ElastiCache metrics

| Metric | Alarm threshold |
|--------|----------------|
| `DatabaseMemoryUsagePercentage` | > 80% |
| `CacheHits` / `CacheMisses` | Monitor ratio — alert if miss rate spikes |

---

## End-to-end duration metric

Log structured JSON at Stage 2 (upload detection) and Stage 9 (PipelineComplete).
A CloudWatch Metric Filter extracts `docId` and `timestamp` from both log lines
and computes `PipelineComplete.timestamp - PipelineStart.timestamp`.

```python
# In notify.py — log pipeline duration
logger.info(json.dumps({
    "event": "PipelineComplete",
    "docId": doc_id,
    "status": status,
    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
}))
```

Metric filter pattern (CloudWatch):

```
{ $.event = "PipelineComplete" }
```

Emit `PipelineDuration` metric with value from `$.durationMs` (add this field in
the `notify.py` log — compute by reading `PipelineStart` timestamp from Redis or
the S3 object upload timestamp from the EventBridge event).

---

## Alarms

### DLQ depth — critical

```python
cloudwatch.Alarm(
    self, "DLQDepthAlarm",
    metric=dlq.metric_approximate_number_of_messages_visible(),
    threshold=0,
    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    evaluation_periods=1,
    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
    alarm_description="DLQ has messages — document processing failure requires investigation",
)
```

SNS action: page on-call immediately.

### Agent latency — warning

```python
cloudwatch.Alarm(
    self, "AgentLatencyAlarm",
    metric=cloudwatch.Metric(
        namespace="DefraPipeline",
        metric_name="AgentDuration",
        statistic="p99",
        period=Duration.minutes(5),
    ),
    threshold=60_000,   # 60 seconds p99
    evaluation_periods=3,
    alarm_description="Agent p99 latency > 60s — Claude API may be degraded",
)
```

### Queue backlog — warning

```python
cloudwatch.Alarm(
    self, "QueueBacklogAlarm",
    metric=queue.metric_approximate_number_of_messages_visible(),
    threshold=50,
    evaluation_periods=2,
    alarm_description="More than 50 documents queued — processing may be falling behind",
)
```

### Agent queue alarms

These alarms monitor the SQS Tasks and Status queues used by Stage 6 agents.

```python
# TasksOldestMessage — message age > 10 min means agent may be hung
cloudwatch.Alarm(self, "TasksOldestMessageAlarm",
    metric=tasks_queue.metric_approximate_age_of_oldest_message(),
    threshold=600,  # 10 minutes
    evaluation_periods=1,
    alarm_description="Agent task age > 10 min — agent Lambda may be hung",
)

# TasksQueueDepth — depth > 50 means invocations not keeping up
cloudwatch.Alarm(self, "TasksQueueDepthAlarm",
    metric=tasks_queue.metric_approximate_number_of_messages_visible(),
    threshold=50,
    evaluation_periods=2,
    alarm_description="More than 50 agent tasks queued — invocations falling behind",
)

# AgentFailureRate — > 2 failures in any 5-min window
cloudwatch.Alarm(self, "AgentFailureRateAlarm",
    metric=cloudwatch.Metric(namespace="DefraPipeline", metric_name="AgentFailure",
        statistic="Sum", period=Duration.minutes(5)),
    threshold=2,
    evaluation_periods=1,
    alarm_description="Agent failure rate > 2/5min — Claude API or code issue",
)

# Per-agent DLQ depth — any message is a critical failure
for agent_type in ["security", "data", "risk", "ea", "solution"]:
    cloudwatch.Alarm(self, f"AgentDLQAlarm{agent_type.capitalize()}",
        metric=tasks_dlq.metric_approximate_number_of_messages_visible(
            dimensions_map={"QueueName": f"defra-agent-tasks-dlq"}),
        threshold=0,
        comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluation_periods=1,
        alarm_description=f"DLQ has messages for {agent_type} agent — unrecoverable failure",
    )
```

---

## CloudWatch Dashboard

```python
dashboard = cloudwatch.Dashboard(self, "PipelineDashboard", dashboard_name="DefraP-Pipeline")

dashboard.add_widgets(
    # Row 1: Pipeline health
    cloudwatch.GraphWidget(title="End-to-end Duration (p50/p99)", ...),
    cloudwatch.SingleValueWidget(title="DLQ Depth", ...),
    cloudwatch.SingleValueWidget(title="Queue Backlog", ...),

    # Row 2: Per-stage error rates
    cloudwatch.GraphWidget(title="Lambda Error Rates", ...),

    # Row 3: Agent latency breakdown
    cloudwatch.GraphWidget(
        title="Agent Duration by Type",
        left=[
            cloudwatch.Metric(namespace="DefraPipeline", metric_name="AgentDuration",
                              dimensions_map={"agentType": t}, statistic="p99")
            for t in ["security", "data", "risk", "ea", "solution"]
        ],
    ),

    # Row 4: Cache efficiency
    cloudwatch.GraphWidget(title="Redis Hit/Miss Rate", ...),
    cloudwatch.GraphWidget(title="Redis Memory Usage %", ...),
)
```

---

## Structured logging convention

Every Lambda handler must use structured JSON logging:

```python
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Usage:
logger.info(json.dumps({
    "event": "StageName",
    "docId": doc_id,
    "agentType": agent_type,   # if applicable
    "durationMs": elapsed_ms,
    "cacheHit": True,
}))
```

This makes every log line queryable via CloudWatch Insights:

```sql
-- Find all documents that took > 90s end-to-end
fields @timestamp, docId, durationMs
| filter event = "PipelineComplete" and durationMs > 90000
| sort durationMs desc
```

---

## Acceptance Criteria

- [ ] DLQ depth alarm configured — SNS action pages on-call at depth > 0
- [ ] All 8 Lambda functions have error rate alarms (> 5 errors / 5 min)
- [ ] `AgentDuration` alarm fires when p99 > 60s over 3 evaluation periods
- [ ] Queue backlog alarm fires when > 50 messages visible
- [ ] Redis memory alarm fires at > 80% usage
- [ ] CloudWatch Dashboard created with all 4 widget rows
- [ ] All Lambda handlers emit structured JSON logs (not plain text)
- [ ] CloudWatch Logs Insights query for end-to-end duration verified against test run
- [ ] `AgentSuccess` and `AgentFailure` custom metrics emitted by Stage 6 with `agentType` dimension
- [ ] `TasksOldestMessage` alarm fires when message age > 10 min
- [ ] `TasksQueueDepth` alarm fires when > 50 agent tasks queued
- [ ] `AgentFailureRate` alarm fires when > 2 failures in any 5-min window
- [ ] Per-agent DLQ depth alarm fires when any message lands on the agent DLQ
- [ ] All alarms have SNS actions (either page on-call or email)
