# scripts/reset-datapipeline.ps1
#
# Truncate the three mutable data-pipeline tables, confirm they are empty,
# then run the data-pipeline to re-populate them from source_policy_docs.
#
# Usage (run from repo root or scripts/ folder):
#   .\scripts\reset-datapipeline.ps1
#
# Requirements:
#   * .env in the repo root with DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
#   * .venv built from requirements.txt (needs python-dotenv)
#   * psql.exe on PATH  OR  python psycopg2 available in .venv
#
# After the script completes:
#   * data_pipeline.policy_document_sync  — 0 rows
#   * data_pipeline.questions             — 0 rows
#   * data_pipeline.policy_documents      — 0 rows
#   * data_pipeline.source_policy_docs    — unchanged (seed data)
#   * policy_documents + questions        — re-populated by the pipeline run

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Resolve repo root ──────────────────────────────────────────────────────────
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

# ── Colour helpers ─────────────────────────────────────────────────────────────
function ok($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host ([char]0x2713) -ForegroundColor Green -NoNewline
    Write-Host ("  {0,-38} {1}" -f $label, $detail)
}
function fail($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host ([char]0x2717) -ForegroundColor Red -NoNewline
    Write-Host ("  {0,-38} {1}" -f $label, $detail)
}
function warn($label, $detail = '') {
    Write-Host "  " -NoNewline
    Write-Host "!" -ForegroundColor Yellow -NoNewline
    Write-Host ("  {0,-38} {1}" -f $label, $detail)
}
function banner($text) {
    Write-Host ""
    Write-Host $text -ForegroundColor White
    Write-Host ("-" * 58)
}

# ── Require .venv ──────────────────────────────────────────────────────────────
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: .venv not found at $VenvPython" -ForegroundColor Red
    Write-Host "  Build it:  python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

# ── Load .env via python-dotenv ────────────────────────────────────────────────
# Python handles special characters in passwords (e.g. Admin123$@) safely.
$EnvFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "ERROR: .env not found at $EnvFile" -ForegroundColor Red
    exit 1
}

$EnvVars = & $VenvPython -c @"
import json, sys
from dotenv import dotenv_values
vals = {k: v for k, v in dotenv_values(r'$EnvFile').items() if v is not None}
print(json.dumps(vals))
"@ 2>$null

if ($LASTEXITCODE -ne 0 -or -not $EnvVars) {
    Write-Host "ERROR: failed to load .env — is python-dotenv installed in .venv?" -ForegroundColor Red
    exit 1
}

$Parsed = $EnvVars | ConvertFrom-Json
foreach ($prop in $Parsed.PSObject.Properties) {
    Set-Item -Path "Env:\$($prop.Name)" -Value $prop.Value
}

# ── Resolve DB variables (with safe defaults) ──────────────────────────────────
$DbHost = if ($env:DB_HOST)     { $env:DB_HOST }     else { "localhost" }
$DbPort = if ($env:DB_PORT)     { $env:DB_PORT }     else { "5432" }
$DbName = if ($env:DB_NAME)     { $env:DB_NAME }     else { "aiadocuments" }
$DbUser = if ($env:DB_USER)     { $env:DB_USER }     else { "aiauser" }
$DbPass = if ($env:DB_PASSWORD) { $env:DB_PASSWORD } else { "" }

# ── DB helper: run SQL via Python psycopg2 ─────────────────────────────────────
# Using Python avoids psql.exe dependency and handles special chars in passwords.
function Invoke-Sql {
    param(
        [string]$Sql,
        [switch]$ScalarInt   # return a single integer result
    )
    if ($ScalarInt) {
        $result = & $VenvPython -c @"
import psycopg2, sys
conn = psycopg2.connect(host='$DbHost', port=$DbPort, dbname='$DbName', user='$DbUser', password=r'$DbPass')
cur = conn.cursor()
cur.execute("""$Sql""")
print(cur.fetchone()[0])
conn.close()
"@ 2>&1
        if ($LASTEXITCODE -ne 0) { return -1 }
        return [int]($result -as [string]).Trim()
    } else {
        $output = & $VenvPython -c @"
import psycopg2, sys
conn = psycopg2.connect(host='$DbHost', port=$DbPort, dbname='$DbName', user='$DbUser', password=r'$DbPass')
conn.autocommit = True
cur = conn.cursor()
cur.execute("""$Sql""")
conn.close()
print('OK')
"@ 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host $output -ForegroundColor Red
            return $false
        }
        return $true
    }
}

function Get-RowCount($Table) {
    return Invoke-Sql -Sql "SELECT COUNT(*) FROM $Table;" -ScalarInt
}

Set-Location $RepoRoot

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 1 — verify PostgreSQL connection"

$connTest = & $VenvPython -c @"
import psycopg2, sys
try:
    conn = psycopg2.connect(host='$DbHost', port=$DbPort, dbname='$DbName', user='$DbUser', password=r'$DbPass')
    conn.close()
    print('OK')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"@ 2>&1

if ($LASTEXITCODE -ne 0) {
    fail "PostgreSQL" "cannot connect — check DB_* vars in .env"
    Write-Host $connTest -ForegroundColor Red
    exit 1
}
ok "PostgreSQL" "${DbUser}@${DbHost}:${DbPort}/${DbName}"

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 2 — truncate mutable tables (CASCADE)"

$TruncateSql = @"
TRUNCATE TABLE
    data_pipeline.policy_document_sync,
    data_pipeline.questions,
    data_pipeline.policy_documents
CASCADE;
"@

$truncOk = Invoke-Sql -Sql $TruncateSql
if (-not $truncOk) {
    fail "Truncate failed" "see error above"
    exit 1
}
ok "Truncated" "policy_document_sync, questions, policy_documents"

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 3 — confirm row counts"

$failCount = 0

function Assert-Empty($Table) {
    $n = Get-RowCount $Table
    if ($n -eq 0) {
        ok $Table "0 rows"
    } else {
        fail $Table "$n rows (expected 0)"
        $script:failCount++
    }
}

Assert-Empty "data_pipeline.policy_document_sync"
Assert-Empty "data_pipeline.questions"
Assert-Empty "data_pipeline.policy_documents"

$seedCount = Get-RowCount "data_pipeline.source_policy_docs"
if ($seedCount -gt 0) {
    ok "data_pipeline.source_policy_docs" "$seedCount rows (seed intact)"
} else {
    warn "data_pipeline.source_policy_docs" "0 rows — seed table is empty; pipeline will produce no output"
}

if ($failCount -gt 0) {
    Write-Host ""
    Write-Host "Truncate verification failed for $failCount table(s). Aborting pipeline run." -ForegroundColor Red
    exit 1
}

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 4 — run data pipeline"

Write-Host ""
Write-Host "  `$ .venv\Scripts\python -m app.datapipeline.src.main"
Write-Host ""

& $VenvPython -m app.datapipeline.src.main
$pipelineExit = $LASTEXITCODE

Write-Host ""
if ($pipelineExit -eq 0) {
    ok "Pipeline exited cleanly" "exit code 0"
} else {
    fail "Pipeline exited with errors" "exit code $pipelineExit"
}

# ──────────────────────────────────────────────────────────────────────────────
banner "Step 5 — final row counts"

$pdCount   = Get-RowCount "data_pipeline.policy_documents"
$qCount    = Get-RowCount "data_pipeline.questions"
$syncCount = Get-RowCount "data_pipeline.policy_document_sync"

if ($pdCount -gt 0) {
    ok "policy_documents" "$pdCount rows"
} else {
    warn "policy_documents" "0 rows — pipeline may not have produced output"
}

if ($qCount -gt 0) {
    ok "questions" "$qCount rows"
} else {
    warn "questions" "0 rows — pipeline may not have produced output"
}

ok "policy_document_sync" "$syncCount rows"
ok "source_policy_docs"   "$seedCount rows (unchanged)"

Write-Host ""
if ($pipelineExit -eq 0) {
    Write-Host "Reset complete." -ForegroundColor White
} else {
    Write-Host "Reset finished with pipeline errors. Check the output above." -ForegroundColor Yellow
    exit $pipelineExit
}
