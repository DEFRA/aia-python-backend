# Build Plans

Numbered in execution order. Each plan is self-contained with its own acceptance criteria.
Complete each plan before starting the next — later plans import code written in earlier ones.

| # | Plan | Delivers | Blocked until |
|---|------|----------|---------------|
| 01 | [Src tree restructure](01-src-tree-restructure.md) | Full `src/` skeleton with stub files | — |
| 02 | [Shared infrastructure](02-shared-infrastructure.md) | `redis_client.py`, `eventbridge.py`, config classes, EventBridge Pydantic models | 01 |
| 03 | [Parse Lambda](03-parse-lambda.md) | Stage 3: PDF/DOCX → chunks → Redis → `DocumentParsed` | 01, 02 |
| 04 | [Tagging Lambda](04-tagging-lambda.md) | Stage 4: chunks → tagged chunks → Redis → `DocumentTagged` | 01, 02, 03 |
| 05 | [Extract Sections Lambda](05-extract-sections-lambda.md) | Stage 5: tagged chunks → 5× section payloads → fan-out `SectionsReady` | 01, 02, 04 |
| 06 | [Specialist Agents](06-specialist-agents.md) | Stage 6: 4 new agents + prompts + SQS-triggered `agent.py` handler (SQS Tasks → SQS Status) | 01, 05 |
| 07 | [Compile Lambda](07-compile-lambda.md) | Stage 7: all 5 results → `CompiledResult` JSON → `DocumentCompiled` | 01, 02, 06 (SQS Status messages from Stage 6) |
| 08 | [Persist + S3 Move Lambdas](08-persist-and-move-lambdas.md) | Stage 8: PostgreSQL write + S3 object move → `FinaliseReady` | 01, 02, 07 |
| 09 | [Notify Lambda](09-notify-lambda.md) | Stage 9: SNS + SQS delete + Redis cleanup → `PipelineComplete` | 01, 02, 08 |
| 10 | [AWS Infrastructure](10-aws-infrastructure.md) | CDK stack: S3, SQS, EventBridge, Lambda, RDS, Redis, SNS, IAM | 03–09 |
| 11 | [Observability](11-observability.md) | CloudWatch alarms, custom metrics, dashboard | 10 |

## New src tree (target state after Plan 01)

```
src/
  config.py                      ← extend in Plan 02
  agents/
    schemas.py                   ← extend in Plans 02, 04, 07
    security_agent.py            ← exists
    tagging_agent.py             ← Plan 04
    data_agent.py  risk_agent.py ea_agent.py  solution_agent.py  ← Plan 06
    prompts/
      security.py                ← exists
      tagging.py                 ← Plan 04
      data.py  risk.py  ea.py  solution.py    ← Plan 06
  db/
    questions_repo.py            ← exists
    results_repo.py              ← Plan 08
  handlers/                      ← new directory
    parse.py                     ← Plan 03
    tag.py                       ← Plan 04
    extract_sections.py          ← Plan 05
    agent.py                     ← Plan 06
    compile.py                   ← Plan 07
    persist.py  s3_move.py       ← Plan 08
    notify.py                    ← Plan 09
  utils/
    helpers.py  pdf_creator*.py  ← exist
    document_parser.py           ← Plan 03
    exceptions.py                ← Plan 03
    redis_client.py              ← Plan 02
    eventbridge.py               ← Plan 02
```
