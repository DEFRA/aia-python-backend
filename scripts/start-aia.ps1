# scripts/start-aia.ps1
#
# Verify S3 / SQS / Bedrock / PostgreSQL connectivity, then start all three
# AIA backend services as background jobs with per-service log files.
#
# Usage (run from repo root or scripts\ folder):
#   .\scripts\start-aia.ps1            # checks + start all services
#   .\scripts\start-aia.ps1 --check    # connectivity checks only, no start
#   .\scripts\start-aia.ps1 --stop     # stop services from a previous run
#   .\scripts\start-aia.ps1 --logs     # tail all three service logs (Ctrl-C to exit)
#
# Requirements:
#   * .env in the repo root with AWS_*, S3_BUCKET_NAME, TASK/STATUS_QUEUE_URL,
#     POSTGRES_URI (or DB_* vars), and LLM_PROVIDER=bedrock
#   * .venv built from requirements.txt
#   * Podman PostgreSQL container running
#
# STS credentials expire every few hours. Re-run after refreshing .env.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Resolve repo root ──────────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$VenvPython  = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvUvicorn = Join-Path $RepoRoot ".venv\Scripts\uvicorn.exe"
$LogDir      = Join-Path $RepoRoot "logs"
$PidFile     = Join-Path $RepoRoot ".aia.pids"

# ── Colour helpers ─────────────────────────────────────────────────────────────
function ok($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host ([char]0x2713) -ForegroundColor Green -NoNewline
    Write-Host ("  {0,-30} {1}" -f $label, $detail)
}
function fail($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host ([char]0x2717) -ForegroundColor Red -NoNewline
    Write-Host ("  {0,-30} {1}" -f $label, $detail)
}
function warn($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host "!" -ForegroundColor Yellow -NoNewline
    Write-Host ("  {0,-30} {1}" -f $label, $detail)
}
function info($label, $detail = '') {
    Write-Host ("       {0,-30} {1}" -f $label, $detail)
}
function banner($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor White
    Write-Host ("-" * 58)
}

# ── Python check helper ────────────────────────────────────────────────────────
# PS 5.1 mangles double-quotes when passing strings to native commands via -c,
# and converts native stderr to ErrorRecords when $ErrorActionPreference=Stop.
# Write to a temp file to avoid both issues.
function Invoke-PythonCheck([string]$Script) {
    $tmp = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content $tmp $Script -Encoding UTF8
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = & $VenvPython $tmp 2>&1
    $ErrorActionPreference = $prev
    Remove-Item $tmp -ErrorAction SilentlyContinue
    return $out
}

# ── Check .venv ────────────────────────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: .venv not found at $VenvPython" -ForegroundColor Red
    Write-Host "  Build it:  python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# ── Load .env via python-dotenv ────────────────────────────────────────────────
$EnvFile = Join-Path $RepoRoot ".env"
if (Test-Path $EnvFile) {
    $EnvJson = & $VenvPython -c @"
import json
from dotenv import dotenv_values
vals = {k: v for k, v in dotenv_values(r'$EnvFile').items() if v is not None}
print(json.dumps(vals))
"@ 2>$null
    if ($LASTEXITCODE -eq 0 -and $EnvJson) {
        $Parsed = $EnvJson | ConvertFrom-Json
        foreach ($prop in $Parsed.PSObject.Properties) {
            Set-Item -Path "Env:\$($prop.Name)" -Value $prop.Value
        }
    }
}

# ── Parse argument ─────────────────────────────────────────────────────────────
$Mode = if ($args.Count -gt 0) { $args[0] } else { "--start" }

# ── --logs mode ────────────────────────────────────────────────────────────────
if ($Mode -eq "--logs") {
    $logFiles = @(
        (Join-Path $LogDir "core-backend.log"),
        (Join-Path $LogDir "core-backend.err"),
        (Join-Path $LogDir "orchestrator.log"),
        (Join-Path $LogDir "orchestrator.err"),
        (Join-Path $LogDir "relay-service.log"),
        (Join-Path $LogDir "relay-service.err")
    )
    Write-Host "Tailing logs — press Ctrl-C to stop" -ForegroundColor Yellow
    Write-Host ""
    # PowerShell Get-Content -Wait tails one file; for multi-file we start jobs.
    $jobs = foreach ($f in $logFiles) {
        if (-not (Test-Path $f)) { New-Item $f -ItemType File -Force | Out-Null }
        $name = Split-Path $f -Leaf
        Start-Job -ScriptBlock {
            param($path, $label)
            Get-Content -Path $path -Wait | ForEach-Object { "[$label] $_" }
        } -ArgumentList $f, $name
    }
    try {
        while ($true) {
            $jobs | Receive-Job
            Start-Sleep -Milliseconds 300
        }
    } finally {
        $jobs | Stop-Job
        $jobs | Remove-Job
    }
    exit 0
}

# ── --stop mode ────────────────────────────────────────────────────────────────
if ($Mode -eq "--stop") {
    banner "Stopping AIA backend services"
    if (-not (Test-Path $PidFile)) {
        warn "No PID file found" "nothing to stop"
        exit 0
    }
    Get-Content $PidFile | Where-Object { $_ -match ":" } | ForEach-Object {
        $parts   = $_ -split ":"
        $svcName = $parts[0]
        $svcPid  = [int]$parts[1]
        $proc    = Get-Process -Id $svcPid -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $svcPid -Force
            ok $svcName "PID $svcPid stopped"
        } else {
            warn $svcName "PID $svcPid already gone"
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host ""
    ok "Done" ""
    exit 0
}

# ── Change to repo root ────────────────────────────────────────────────────────
Set-Location $RepoRoot

# ─────────────────────────────────────────────────────────────────────────────
banner "Connectivity checks"
$checksOk = $true

# ── PostgreSQL ─────────────────────────────────────────────────────────────────
$pgScript = @'
import asyncio, asyncpg, os, sys

async def main():
    uri = os.environ.get("POSTGRES_URI") or (
        "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
            user=os.environ.get("DB_USER", "aiauser"),
            pw=os.environ.get("DB_PASSWORD", "Admin123$"),
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            db=os.environ.get("DB_NAME", "aiadocuments"),
        )
    )
    try:
        conn = await asyncpg.connect(uri, timeout=5)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM data_pipeline.questions WHERE isactive = TRUE"
    )
    await conn.close()
    print(f"connected — {row['n']} active questions in data_pipeline.questions")

asyncio.run(main())
'@
$pgOut = Invoke-PythonCheck $pgScript
if ($pgOut -match "^connected") {
    ok "PostgreSQL" ($pgOut -join " ")
} else {
    fail "PostgreSQL" ($pgOut -join " ")
    info "" "Start container:  .\scripts\start-datapipeline-dev.ps1"
    $checksOk = $false
}

# ── S3 ────────────────────────────────────────────────────────────────────────
$s3Script = @'
import boto3, os, sys
from botocore.exceptions import ClientError

bucket = os.environ.get("S3_BUCKET_NAME", "pocldnaia001")
region = os.environ.get("AWS_DEFAULT_REGION", "eu-west-2")
s3 = boto3.client(
    "s3",
    region_name=region,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    s3.head_bucket(Bucket=bucket)
    print(f"accessible — s3://{bucket} in {region}")
except ClientError as exc:
    code = exc.response["Error"]["Code"]
    print(f"FAIL ({code}): {exc}", file=sys.stderr)
    sys.exit(1)
'@
$s3Out = Invoke-PythonCheck $s3Script
if ($s3Out -match "^accessible") {
    ok "S3" ($s3Out -join " ")
} else {
    fail "S3" ($s3Out -join " ")
    info "" "Check AWS credentials / bucket name in .env"
    $checksOk = $false
}

# ── SQS — tasks queue ─────────────────────────────────────────────────────────
$sqsTasksScript = @'
import boto3, os, sys
from botocore.exceptions import ClientError

url = os.environ.get("TASK_QUEUE_URL", "")
if not url:
    print("FAIL: TASK_QUEUE_URL not set", file=sys.stderr)
    sys.exit(1)

sqs = boto3.client(
    "sqs",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible  = attrs.get("ApproximateNumberOfMessages", "?")
    inflight = attrs.get("ApproximateNumberOfMessagesNotVisible", "?")
    print(f"reachable — ~{visible} waiting, ~{inflight} in-flight")
except ClientError as exc:
    print(f"FAIL: {exc}", file=sys.stderr)
    sys.exit(1)
'@
$sqsTasksOut = Invoke-PythonCheck $sqsTasksScript
if ($sqsTasksOut -match "^reachable") {
    ok "SQS tasks queue" ($sqsTasksOut -join " ")
} else {
    fail "SQS tasks queue" ($sqsTasksOut -join " ")
    info "" "Check TASK_QUEUE_URL and credentials in .env"
    $checksOk = $false
}

# ── SQS — status queue ────────────────────────────────────────────────────────
$sqsStatusScript = @'
import boto3, os, sys
from botocore.exceptions import ClientError

url = os.environ.get("STATUS_QUEUE_URL", "")
if not url:
    print("FAIL: STATUS_QUEUE_URL not set", file=sys.stderr)
    sys.exit(1)

sqs = boto3.client(
    "sqs",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
)
try:
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    visible  = attrs.get("ApproximateNumberOfMessages", "?")
    inflight = attrs.get("ApproximateNumberOfMessagesNotVisible", "?")
    print(f"reachable — ~{visible} waiting, ~{inflight} in-flight")
except ClientError as exc:
    print(f"FAIL: {exc}", file=sys.stderr)
    sys.exit(1)
'@
$sqsStatusOut = Invoke-PythonCheck $sqsStatusScript
if ($sqsStatusOut -match "^reachable") {
    ok "SQS status queue" ($sqsStatusOut -join " ")
} else {
    fail "SQS status queue" ($sqsStatusOut -join " ")
    info "" "Check STATUS_QUEUE_URL and credentials in .env"
    $checksOk = $false
}

# ── Bedrock ───────────────────────────────────────────────────────────────────
$bedrockScript = @'
import anthropic, asyncio, os, sys

async def main():
    client = anthropic.AsyncAnthropicBedrock(
        aws_region=os.environ.get("AWS_DEFAULT_REGION", "eu-west-2"),
        aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.environ.get("AWS_SESSION_TOKEN") or None,
    )
    try:
        msg = await client.messages.create(
            model="anthropic.claude-3-haiku-20240307-v1:0",
            max_tokens=1,
            messages=[{"role": "user", "content": "1"}],
        )
        print(f"OK — Bedrock runtime responding (stop_reason={msg.stop_reason})")
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

asyncio.run(main())
'@
$bedrockOut = Invoke-PythonCheck $bedrockScript
if ($bedrockOut -match "^OK") {
    ok "Bedrock" ($bedrockOut -join " ")
} else {
    fail "Bedrock" ($bedrockOut -join " ")
    info "" "Check AWS credentials, region, and Bedrock model access in .env"
    $checksOk = $false
}

Write-Host ""

# ── Abort if any check failed ──────────────────────────────────────────────────
if (-not $checksOk) {
    Write-Host "One or more checks failed. Fix the issues above, then re-run." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Tip: STS credentials expire — refresh .env if AWS checks fail."
    exit 1
}

ok "All checks passed" ""
Write-Host ""

# ── Check-only mode exits here ─────────────────────────────────────────────────
if ($Mode -eq "--check") {
    exit 0
}

# ─────────────────────────────────────────────────────────────────────────────
banner "Starting AIA backend services"

if (-not (Test-Path $LogDir)) {
    New-Item $LogDir -ItemType Directory -Force | Out-Null
}

# Kill any stale processes from a previous run
@("app.api.main:app", "app.orchestrator.main:app", "app.relay_service.main:app") | ForEach-Object {
    Get-WmiObject Win32_Process -Filter "CommandLine LIKE '%$_%'" -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
Start-Sleep -Seconds 1

# Truncate PID file
Set-Content $PidFile ""

function Start-Service {
    param(
        [string]$Name,
        [string]$Module,
        [int]   $Port,
        [string]$LogFile
    )
    $ErrFile = $LogFile -replace '\.log$', '.err'
    Set-Content $LogFile ""
    Set-Content $ErrFile ""

    $proc = Start-Process `
        -FilePath $VenvUvicorn `
        -ArgumentList $Module, "--host", "127.0.0.1", "--port", $Port `
        -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $LogFile `
        -RedirectStandardError  $ErrFile `
        -NoNewWindow `
        -PassThru

    Add-Content $PidFile "${Name}:$($proc.Id)"
    $logName = Split-Path $LogFile -Leaf
    info $Name "port=$Port  PID=$($proc.Id)  log=logs\$logName"
}

Start-Service "core-backend"  "app.api.main:app"           8086 (Join-Path $LogDir "core-backend.log")
Start-Service "orchestrator"  "app.orchestrator.main:app"  8001 (Join-Path $LogDir "orchestrator.log")
Start-Service "relay-service" "app.relay_service.main:app" 8002 (Join-Path $LogDir "relay-service.log")

# ── Wait then verify all three survived startup ────────────────────────────────
Start-Sleep -Seconds 2
Write-Host ""
$allUp = $true

Get-Content $PidFile | Where-Object { $_ -match ":" } | ForEach-Object {
    $parts   = $_ -split ":"
    $svcName = $parts[0]
    $svcPid  = [int]$parts[1]
    $proc    = Get-Process -Id $svcPid -ErrorAction SilentlyContinue
    if ($proc) {
        ok $svcName "PID $svcPid — running"
    } else {
        fail $svcName "PID $svcPid — exited at startup"
        info "" "Check logs\$svcName.log for the error"
        $script:allUp = $false
    }
}

Write-Host ""

if ($allUp) {
    Write-Host "All services running." -ForegroundColor White
    Write-Host ""
    Write-Host "  Core Backend   ->  http://127.0.0.1:8086/health"
    Write-Host "  Orchestrator   ->  http://127.0.0.1:8001"
    Write-Host "  Relay Service  ->  http://127.0.0.1:8002/health"
    Write-Host ""
    Write-Host "  Follow logs:   .\scripts\start-aia.ps1 --logs"
    Write-Host "  Stop all:      .\scripts\start-aia.ps1 --stop"
} else {
    Write-Host "One or more services failed to start." -ForegroundColor Red
    exit 1
}
