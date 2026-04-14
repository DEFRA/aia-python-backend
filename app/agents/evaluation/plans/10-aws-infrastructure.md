# Plan 10 — AWS Infrastructure

**Priority:** 10 (deploy after all Lambda code is complete and tested)

**Depends on:** Plans 03–09 (all Lambda handlers implemented)

---

## Goal

Define and provision all AWS infrastructure required to run the pipeline in production:

- S3 bucket with event notifications
- SQS FIFO queue + DLQ
- EventBridge custom bus + 8 routing rules
- Lambda function definitions (one per handler)
- RDS PostgreSQL + RDS Proxy
- ElastiCache Redis cluster
- SNS topic
- IAM roles and policies

---

## Recommended approach: AWS CDK (Python)

Use AWS CDK (Python) to define infrastructure as code. Create a top-level `infra/`
directory separate from `src/`:

```
infra/
  app.py               ← CDK app entry point
  stacks/
    pipeline_stack.py  ← main stack: all resources
  requirements.txt     ← aws-cdk-lib, constructs
  cdk.json
```

---

## S3 Bucket

```python
bucket = s3.Bucket(
    self, "PipelineBucket",
    bucket_name=f"defra-pipeline-{env}",
    versioned=True,
    encryption=s3.BucketEncryption.S3_MANAGED,
    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
    event_bridge_enabled=True,   # native S3 → EventBridge integration
    lifecycle_rules=[
        s3.LifecycleRule(
            id="ExpireInProgress",
            prefix="in_progress/",
            expiration=Duration.days(7),  # safety net for stuck documents
        )
    ],
)
```

Prefixes used:
- `in_progress/` — documents being processed
- `completed/` — successfully processed
- `error/` — failed documents

---

## SQS FIFO Queue + DLQ

```python
dlq = sqs.Queue(
    self, "PipelineDLQ",
    queue_name="defra-pipeline-dlq.fifo",
    fifo=True,
    retention_period=Duration.days(14),
)

queue = sqs.Queue(
    self, "PipelineQueue",
    queue_name="defra-pipeline.fifo",
    fifo=True,
    content_based_deduplication=True,
    visibility_timeout=Duration.seconds(900),   # 15 min — covers full pipeline duration
    dead_letter_queue=sqs.DeadLetterQueue(
        max_receive_count=3,
        queue=dlq,
    ),
)
```

**Visibility timeout = 900s:** The SQS message stays invisible for the entire
pipeline duration. Stage 9 explicitly deletes it on success.

---

## SQS Tasks + Status Queues

Stage 6 agents consume from SQS Tasks and publish results to SQS Status. Both are
Standard queues (not FIFO) with their own DLQs.

```python
# SQS Tasks — Standard queue, agents consume via event source mapping
tasks_dlq = sqs.Queue(self, "AgentTasksDLQ", queue_name="defra-agent-tasks-dlq", retention_period=Duration.days(14))
tasks_queue = sqs.Queue(
    self, "AgentTasksQueue",
    queue_name="defra-agent-tasks",
    visibility_timeout=Duration.minutes(15),   # matches Lambda timeout
    dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=tasks_dlq),
)

# SQS Status — Standard queue, compile Lambda consumes via event source mapping
status_dlq = sqs.Queue(self, "AgentStatusDLQ", queue_name="defra-agent-status-dlq", retention_period=Duration.days(14))
status_queue = sqs.Queue(
    self, "AgentStatusQueue",
    queue_name="defra-agent-status",
    visibility_timeout=Duration.seconds(300),
    dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=status_dlq),
)
```

---

## EventBridge Custom Bus + Rules

```python
bus = events.EventBus(self, "PipelineBus", event_bus_name="defra-pipeline")
```

### Rules reference

| Rule name | Event pattern | Target |
|-----------|--------------|--------|
| `on-document-uploaded` | S3 Object Created in `in_progress/` | SQS FIFO Queue |
| `on-document-parsed` | `detail-type: DocumentParsed` | Parse Lambda |
| `on-document-tagged` | `detail-type: DocumentTagged` | Tag Lambda |
| `on-document-compiled-persist` | `detail-type: DocumentCompiled` | Persist Lambda |
| `on-document-compiled-move` | `detail-type: DocumentCompiled` | S3 Move Lambda |
| `on-finalise-ready` | `detail-type: FinaliseReady` | Notify Lambda |

> **Note:** Stage 6 agents are triggered by SQS Tasks (event source mapping), not
> EventBridge. The compile Lambda (Stage 7) is triggered by SQS Status. See Plan 06
> and Plan 07 respectively.

---

## Lambda Functions

One CDK `Function` construct per handler:

```python
common_props = dict(
    runtime=lambda_.Runtime.PYTHON_3_12,
    handler="handler.lambda_handler",
    timeout=Duration.seconds(300),
    memory_size=512,
    environment={
        "REDIS_HOST": redis_cluster.attr_redis_endpoint_address,
        "EVENTBRIDGE_BUS_NAME": "defra-pipeline",
        "S3_BUCKET": bucket.bucket_name,
        "SQS_QUEUE_URL": queue.queue_url,
        "SNS_TOPIC_ARN": topic.topic_arn,
        "ANTHROPIC_API_KEY": "{{resolve:secretsmanager:defra/anthropic-key}}",
        # DB vars injected from Secrets Manager
    },
)

parse_fn   = lambda_.Function(self, "ParseFn",   code=lambda_.Code.from_asset("src/handlers/parse.py"), **common_props)
tag_fn     = lambda_.Function(self, "TagFn",     code=lambda_.Code.from_asset("src/handlers/tag.py"), **common_props)
extract_fn = lambda_.Function(self, "ExtractFn", code=lambda_.Code.from_asset("src/handlers/extract_sections.py"), **common_props)
compile_fn = lambda_.Function(self, "CompileFn", code=lambda_.Code.from_asset("src/handlers/compile.py"), **common_props)
persist_fn = lambda_.Function(self, "PersistFn", code=lambda_.Code.from_asset("src/handlers/persist.py"), **common_props)
s3move_fn  = lambda_.Function(self, "S3MoveFn",  code=lambda_.Code.from_asset("src/handlers/s3_move.py"), **common_props)
notify_fn  = lambda_.Function(self, "NotifyFn",  code=lambda_.Code.from_asset("src/handlers/notify.py"), **common_props)

# --- 5 Agent Lambda functions (SQS Tasks → SQS Status) ---
for agent_type in ["security", "data", "risk", "ea", "solution"]:
    fn = lambda_.Function(self, f"AgentFn{agent_type.capitalize()}",
        function_name=f"agent-{agent_type}",
        code=lambda_.Code.from_asset("src/handlers/agent.py"),
        memory_size=512,
        timeout=Duration.minutes(15),
        reserved_concurrent_executions=10,
        environment={**common_env, "AGENT_TYPE": agent_type, "STATUS_QUEUE_URL": status_queue.queue_url},
        **common_props,
    )
    fn.add_event_source(lambda_event_sources.SqsEventSource(tasks_queue, batch_size=1, filters=[
        lambda_.FilterCriteria.filter({"body": {"agentType": lambda_.FilterRule.is_equal(agent_type)}})
    ]))
    tasks_queue.grant_consume_messages(fn)
    status_queue.grant_send_messages(fn)

# --- Compile Lambda consumes from SQS Status ---
compile_fn.add_event_source(lambda_event_sources.SqsEventSource(status_queue, batch_size=1))
status_queue.grant_consume_messages(compile_fn)
```

**Agent Lambdas use 512 MB memory and 15-min timeout** — Claude API calls are
network-bound. Each agent type has its own function with reserved concurrency and
SQS event source filtering by `agentType`.

---

## RDS PostgreSQL + RDS Proxy

```python
db_cluster = rds.DatabaseCluster(
    self, "PipelineDB",
    engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_15_4),
    writer=rds.ClusterInstance.serverless_v2("writer"),
    vpc=vpc,
    credentials=rds.Credentials.from_generated_secret("defra_pipeline"),
)

proxy = rds.DatabaseProxy(
    self, "DBProxy",
    proxy_target=rds.ProxyTarget.from_cluster(db_cluster),
    secrets=[db_cluster.secret],
    vpc=vpc,
)
```

### Schema (run once at deploy time via migration script)

```sql
-- checklist_questions: seeded from evaluation_questions.txt
CREATE TABLE checklist_questions (
    id         SERIAL PRIMARY KEY,
    agent_type TEXT NOT NULL,
    question   TEXT NOT NULL,
    active     BOOLEAN DEFAULT TRUE
);

-- assessment_results: written by Stage 8a Persist Lambda
CREATE TABLE assessment_results (
    doc_id        UUID PRIMARY KEY,
    doc_type      TEXT,
    generated_at  TIMESTAMPTZ,
    processed_at  TIMESTAMPTZ,
    status        TEXT,
    result_json   JSONB
);
```

---

## ElastiCache Redis

```python
redis_sg = ec2.SecurityGroup(self, "RedisSG", vpc=vpc)
redis_cluster = elasticache.CfnReplicationGroup(
    self, "PipelineRedis",
    replication_group_description="Defra pipeline state store",
    engine="redis",
    cache_node_type="cache.t3.medium",
    num_cache_clusters=2,   # primary + one replica
    at_rest_encryption_enabled=True,
    transit_encryption_enabled=True,
    automatic_failover_enabled=True,
)
```

---

## SNS Topic

```python
topic = sns.Topic(self, "PipelineTopic", topic_name="defra-pipeline-notify")
# Subscription for front-end webhook (URL configured separately)
topic.add_subscription(subscriptions.UrlSubscription(os.environ["FRONTEND_WEBHOOK_URL"]))
```

---

## IAM

Grant least-privilege permissions on each Lambda:

```python
bucket.grant_read_write(parse_fn)    # Stage 3 reads document
bucket.grant_read_write(s3move_fn)   # Stage 8b moves document
queue.grant_consume_messages(parse_fn)   # Stage 3 polls SQS
queue.grant_send_messages(notify_fn)     # Stage 9 deletes message
topic.grant_publish(notify_fn)           # Stage 9 SNS publish
bus.grant_put_events_to(parse_fn, tag_fn, extract_fn, compile_fn, persist_fn, s3move_fn, notify_fn)
db_cluster.secret.grant_read(persist_fn)
db_cluster.secret.grant_read(extract_fn) # Stage 5 loads questions from DB
# Agent Lambda SQS grants are configured in the per-agent loop above (tasks_queue.grant_consume_messages, status_queue.grant_send_messages)
# Compile Lambda SQS grants are configured above (status_queue.grant_consume_messages)
```

---

## Acceptance Criteria

- [ ] `infra/` CDK app synthesises without errors (`cdk synth`)
- [ ] S3 bucket has EventBridge integration enabled
- [ ] SQS FIFO queue has 900s visibility timeout and DLQ with 3 max receive count
- [ ] SQS Tasks queue with 15-min visibility timeout and DLQ with 3 max receive count
- [ ] SQS Status queue with 300s visibility timeout and DLQ with 3 max receive count
- [ ] 6 EventBridge rules created (no `on-sections-ready` — agents use SQS Tasks)
- [ ] 5 Agent Lambda functions (`agent-security`, ..., `agent-solution`) with SQS event source mapping, batch size 1, filtered by `agentType`
- [ ] Compile Lambda event-source-mapped to SQS Status (batch size 1)
- [ ] Lambda functions use Python 3.12; agent functions have 512 MB memory, 15-min timeout
- [ ] RDS PostgreSQL schema applied via migration script
- [ ] Redis cluster has in-transit and at-rest encryption enabled
- [ ] All IAM grants are least-privilege (no `*` actions or resources)
- [ ] `cdk deploy` succeeds in a staging environment
- [ ] End-to-end smoke test: upload a test PDF → confirm `PipelineComplete` event in CloudWatch
