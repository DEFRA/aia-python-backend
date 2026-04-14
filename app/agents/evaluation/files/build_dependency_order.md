# Build Dependency Order — Security Agent End-to-End Test

Goal: wire up the full pipeline so the security agent can be tested end-to-end.

Plans 01 (src tree) and 02 (shared infra) are complete.

| Order | Plan | Handler(s) | Delivers | Blocked by |
|-------|------|-----------|----------|------------|
| 1 | 03 | `parse.py` | PDF/DOCX → chunks → Redis → `DocumentParsed` event | Nothing — ready now |
| 2 | 04 | `tag.py` + `TaggedChunk` schema | Chunks → tagged chunks → Redis → `DocumentTagged` event | Plan 03 |
| 3 | 05 | `extract_sections.py` | Tagged chunks → 5 section payloads → fan-out `SectionsReady` | Plan 04 |
| 4 | 06 | `agent.py` handler | `SectionsReady` → security agent → result to Redis → `AgentComplete` | Plan 05 |
| 5 | 07 | `compile.py` + `CompiledResult` schema | Fan-in 5 results → compiled report → `DocumentCompiled` | Plan 06 |
| 6 | 08 | `persist.py` + `s3_move.py` | DB write + S3 move → `FinaliseReady` | Plan 07 |
| 7 | 09 | `notify.py` | SNS + SQS delete + Redis cleanup → `PipelineComplete` | Plan 08 |

Minimum for security agent testing: Plans 03–06 (parse through agent handler).
Plans 07–09 can be deferred — verify results from Redis directly.
