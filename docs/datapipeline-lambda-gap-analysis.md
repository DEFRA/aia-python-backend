# Datapipeline Lambda Deployment Gap Analysis

Date: 2026-05-15  
Scope: Review current repository/workflow structure for Datapipeline-only deployment to AWS Lambda via GitHub Actions, with focus on merge readiness and credential/role risks.  
Constraint followed: read-only analysis of existing code and workflows (no runtime execution or code fixes).

## Executive Verdict

Current state is **not merge-ready** for reliable Datapipeline Lambda deployment from GitHub Actions.

Primary reason: the deployment workflow is not placed where GitHub Actions will execute it, and credential/role posture is not production-safe yet.

## What Was Reviewed

- `.github/workflows/ci.yml`
- `app/datapipeline/.github/workflows/lambda_deployment.yaml`
- `app/datapipeline/.github/workflows/github_secret.yaml`
- `app/datapipeline/src/lambda_function.py`
- `app/datapipeline/src/main.py`
- `app/datapipeline/src/evaluator.py`
- `app/datapipeline/requirements.txt`
- `app/datapipeline/Readme.md`

## Severity-Ranked Gaps

### CRITICAL

1. Workflow discovery path is wrong for GitHub Actions
- File exists at `app/datapipeline/.github/workflows/lambda_deployment.yaml`.
- GitHub Actions only auto-discovers workflows under repo root: `.github/workflows/`.
- Impact: deployment workflow will not run on push to main.

### HIGH

2. Static AWS keys are used in CI instead of OIDC role assumption
- Workflow uses `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` secrets directly.
- This increases secret management and rotation risk, and can block deployment when keys expire/rotate.
- Recommended posture for Actions: OIDC (`id-token: write`) + `aws-actions/configure-aws-credentials` role assumption.

3. Runtime-required Lambda environment variables are not managed by pipeline
- Datapipeline code requires several environment variables at runtime (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SCHEMA`, `SHAREPOINT_*`, `AWS_DEFAULT_REGION`, `MODEL_ID`, optional `ANTHROPIC_API_KEY`).
- Workflow updates code/config but does not verify or set env variables.
- Impact: deployment may succeed but Lambda runtime can fail immediately.

4. `DB_SCHEMA` is hard-required by code path but omitted from required env validation list
- In `main.py`, `_get_db_connection()` uses `os.environ["DB_SCHEMA"]` directly.
- `_REQUIRED_ENV` in same file does not include `DB_SCHEMA`.
- Impact: runtime failure via `KeyError` in some configurations, even though startup checks may appear to pass.

5. No Datapipeline PR-stage validation in root CI
- Root CI (`.github/workflows/ci.yml`) validates `app/agents/evaluation`, not Datapipeline.
- Impact: Datapipeline packaging/dependency/handler issues can merge undetected.

### MEDIUM

6. Datapipeline workflow triggers only on push to main
- No pull request trigger for deployment readiness checks.
- Impact: merge-time regressions discovered late.

7. Packaging strategy may break local-source feature path in Lambda
- Workflow copies only `app/datapipeline/src/*` into zip root.
- In `main.py`, default local sources path is derived as `Path(__file__).resolve().parent.parent / "data" / "policy_sources.json"`.
- After flattening into `/var/task`, `parent.parent` resolves differently from local repo layout.
- Impact: feature flag `USE_LOCAL_POLICY_SOURCES=true` is likely broken unless path is explicitly overridden and file bundled.

8. Region/function/architecture are hard-coded
- Workflow hard-codes `eu-west-2`, function name `aia-datapipeline`, and `x86_64` architecture.
- Impact: environment portability and multi-account promotion are fragile.

9. Secondary workflow likely non-functional and non-discovered
- `app/datapipeline/.github/workflows/github_secret.yaml` is also outside root workflow path.
- It references `python3 connection_test.py` without setting a working directory.
- Impact: even if moved, command path may fail unless adjusted.

## Credential and Role Risk Assessment

## A. CI/CD deployment identity (GitHub Actions -> AWS)

Minimum effective permissions required for the deploy identity used by Actions:
- `lambda:UpdateFunctionCode`
- `lambda:UpdateFunctionConfiguration`
- `lambda:GetFunction`
- `lambda:GetFunctionConfiguration`
- `lambda:ListVersionsByFunction` (optional but commonly needed in extended flows)

Potential blockers:
- Missing permission on function resource ARN.
- Cross-account deployment without trust policy configured.
- KMS permission issues if Lambda env vars are encrypted with a customer-managed key.

## B. Lambda execution role (runtime)

Datapipeline runtime dependencies imply these access needs:
- CloudWatch Logs write permissions (`logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`).
- Bedrock invoke permission for selected model(s):
  - `bedrock:InvokeModel`
  - `bedrock:InvokeModelWithResponseStream` (if later enabled/required by SDK behavior).
- Network path to PostgreSQL (VPC/subnet/security group/NACL alignment).
- Outbound network path to:
  - Microsoft Graph (SharePoint)
  - AWS Bedrock endpoint in configured region
- Database credentials presence and validity.

Potential blockers:
- Lambda in private subnets without NAT or required VPC endpoints.
- Security groups blocking DB connectivity.
- Bedrock model access not enabled in target account/region.

## Merge Readiness Checklist (Current Status)

- Workflow discoverable by GitHub Actions: **FAIL**
- Datapipeline CI checks in PR: **FAIL**
- Deployment identity security posture (OIDC): **FAIL**
- Runtime env var completeness assurance: **FAIL**
- Runtime IAM/network prerequisites documented and validated: **PARTIAL**
- Lambda handler alignment (`lambda_function.lambda_handler`): **PASS**

## Pre-Merge Challenges to Resolve

1. Ensure deployment workflow is placed under root `.github/workflows/` so GitHub executes it.
2. Move to GitHub OIDC role assumption for AWS instead of static long-lived secrets.
3. Add Datapipeline-specific PR validation (at minimum package build + import/handler checks).
4. Add explicit runtime env var strategy (source of truth and deployment-time validation).
5. Confirm execution role + network route for Bedrock, Graph API, and PostgreSQL.
6. Validate Lambda packaging assumptions against feature flags (`USE_LOCAL_POLICY_SOURCES`).

## Bottom Line

For Datapipeline-only scope, there are **pre-existing structural and IAM/credential gaps** that can cause either:
- no deployment trigger at all, or
- deployment success followed by runtime failure.

Address the listed blockers before merging to main if the goal is reliable Lambda deployment through GitHub Actions.
