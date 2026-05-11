# ECS Deployment Templates

This folder contains starter templates for ECS task definitions used by the CI/CD workflow.

## Files

- `taskdef-backend-stack.json`
- `taskdef-agent.json`

## Recommended Workflow File Name

Use:

- `.github/workflows/ecs-backend-agent-deploy.yml`

Current workflow in this repo can keep its existing name; this is only a naming recommendation.

## Required Replacements

Replace placeholders before first deployment:

- `<AWS_ACCOUNT_ID>`
- `<AWS_REGION>`
- `<BACKEND_IMAGE_URI>`
- `<AGENT_IMAGE_URI>`

Note: the GitHub Action `amazon-ecs-render-task-definition` updates image values at deploy time, so `<BACKEND_IMAGE_URI>` and `<AGENT_IMAGE_URI>` are safe placeholders.

## Suggested GitHub Variables

- `BACKEND_TASKDEF_PATH=ecs/taskdef-backend-stack.json`
- `AGENT_TASKDEF_PATH=ecs/taskdef-agent.json`
- `COREBACKEND_CONTAINER_NAME=corebackend`
- `ORCHESTRATOR_CONTAINER_NAME=orchestrator`
- `AGENT_CONTAINER_NAME=agent-service`

## Architecture Choice

Current templates assume:

- CoreBackend + Orchestrator in one ECS task (two containers)
- Agent service in separate ECS task/service

This is the best fit for your current behavior because CoreBackend invokes Orchestrator over `localhost` and both share the same backing DB.
