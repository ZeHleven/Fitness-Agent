param(
    [string]$Version = "0.5.0"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendPath = (Resolve-Path (Join-Path $repoRoot "backend")).Path
$outputDirectory = Join-Path $repoRoot "deploy\cloudbase"
$outputPath = Join-Path $outputDirectory "fitness-agent-backend-$Version.zip"

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}

& tar -a -c -f $outputPath `
    --exclude=.env `
    --exclude=.venv `
    --exclude=tests `
    --exclude=.pytest_cache `
    --exclude=__pycache__ `
    --exclude='*.pyc' `
    -C $backendPath .
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create CloudBase package."
}

$entries = @(& tar -tf $outputPath)
$forbidden = $entries | Where-Object {
    $_ -match '(^|/)(\.env($|\.)|\.venv|tests|\.pytest_cache|__pycache__)' -or
    $_ -match '\.pyc$'
}
if ($forbidden) {
    Remove-Item -LiteralPath $outputPath -Force
    throw "Package contains forbidden entries: $($forbidden -join ', ')"
}

$required = @(
    "./Dockerfile",
    "./alembic/versions/0013_agent_run_queue.py",
    "./alembic/versions/0014_agent_run_serialization.py",
    "./alembic/versions/0015_agent_conversation_understanding.py",
    "./alembic/versions/0016_agent_run_result_uniqueness.py",
    "./alembic/versions/0017_agent_execution_trace.py",
    "./alembic/versions/0018_agent_tool_call_uniqueness.py",
    "./alembic/versions/0019_agent_intent_error_category.py",
    "./app/services/agent_jobs.py",
    "./app/services/agent_controller.py",
    "./app/services/agent_planner.py",
    "./app/services/agent_trace.py",
    "./app/schemas/agent_planning.py",
    "./app/schemas/agent_trace.py",
    "./scripts/start_server.sh"
)
foreach ($entry in $required) {
    if ($entry -notin $entries) {
        Remove-Item -LiteralPath $outputPath -Force
        throw "Package is missing required entry: $entry"
    }
}

$file = Get-Item -LiteralPath $outputPath
Write-Host "Created $($file.FullName) ($($file.Length) bytes)"
