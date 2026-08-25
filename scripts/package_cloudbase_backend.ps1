param(
    [string]$Version = "0.5.8",
    [ValidateRange(0.01, 1024)]
    [double]$MaxPackageSizeMB = 25
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendPath = (Resolve-Path (Join-Path $repoRoot "backend")).Path
$outputDirectory = Join-Path $repoRoot "deploy\cloudbase"
$outputPath = Join-Path $outputDirectory "fitness-agent-backend-$Version.zip"

$excludedDirectoryNames = @(
    ".git",
    ".venv",
    "venv",
    "tests",
    ".uv-cache",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    ".hypothesis",
    "__pycache__",
    "htmlcov",
    "dist",
    "build",
    "node_modules",
    ".pnpm-store"
)
$excludedFilePatterns = @(
    ".env",
    ".env.*",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.db",
    "*.sqlite3",
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    ".DS_Store",
    "Thumbs.db"
)
$tarExcludePatterns = @($excludedDirectoryNames) + @($excludedFilePatterns) + @("*.egg-info")

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}

$tarArguments = @("-a", "-c", "-f", $outputPath)
foreach ($pattern in $tarExcludePatterns) {
    $tarArguments += "--exclude=$pattern"
}
$tarArguments += @("-C", $backendPath, ".")

& tar @tarArguments
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create CloudBase package."
}

$entries = @(& tar -tf $outputPath)
$forbidden = $entries | Where-Object {
    $normalizedEntry = $_ -replace '\\', '/'
    $normalizedEntry = $normalizedEntry -replace '^\./', ''
    $segments = @($normalizedEntry.Split('/', [System.StringSplitOptions]::RemoveEmptyEntries))
    $leafName = if ($segments.Count -gt 0) { $segments[-1] } else { "" }

    $containsExcludedDirectory = @(
        $segments | Where-Object { $excludedDirectoryNames -contains $_ }
    ).Count -gt 0
    $containsEggInfo = @(
        $segments | Where-Object { $_ -like "*.egg-info" }
    ).Count -gt 0
    $matchesExcludedFile = @(
        $excludedFilePatterns | Where-Object { $leafName -like $_ }
    ).Count -gt 0

    $containsExcludedDirectory -or $containsEggInfo -or $matchesExcludedFile
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
    "./alembic/versions/0020_agent_proposal_lifecycle.py",
    "./app/config.py",
    "./app/routers/agent.py",
    "./app/schemas/agent_plan_adjustment_proposal.py",
    "./app/schemas/agent_plan_adjustment_proposal_api.py",
    "./app/services/agent_jobs.py",
    "./app/services/agent_controller.py",
    "./app/services/agent_planner.py",
    "./app/services/agent_plan_adjustment_proposal_decisions.py",
    "./app/services/agent_plan_adjustment_proposal_execution.py",
    "./app/services/agent_plan_adjustment_proposal_persistence.py",
    "./app/services/agent_plan_adjustment_proposals.py",
    "./app/services/agent_runtime.py",
    "./app/services/agent_tool_registry.py",
    "./app/services/agent_tool_registry_read_authority.py",
    "./app/services/agent_tool_registry_read_enforcement.py",
    "./app/services/agent_tool_registry_shadow.py",
    "./app/services/agent_tool_registry_shadow_facts.py",
    "./app/services/agent_tool_registry_shadow_trace.py",
    "./app/services/agent_tool_registry_shadow_metrics.py",
    "./app/services/agent_tool_registry_shadow_metric_adapter.py",
    "./app/services/agent_tool_registry_shadow_observation.py",
    "./app/services/agent_trace.py",
    "./app/schemas/agent_planning.py",
    "./app/schemas/agent_tool_registry.py",
    "./app/schemas/agent_trace.py",
    "./scripts/summarize_registry_shadow_metrics.py",
    "./scripts/start_server.sh"
)
foreach ($entry in $required) {
    if ($entry -notin $entries) {
        Remove-Item -LiteralPath $outputPath -Force
        throw "Package is missing required entry: $entry"
    }
}

$file = Get-Item -LiteralPath $outputPath
$maxPackageSizeBytes = $MaxPackageSizeMB * 1MB
if ($file.Length -gt $maxPackageSizeBytes) {
    $actualSizeMB = [math]::Round($file.Length / 1MB, 2)
    Remove-Item -LiteralPath $outputPath -Force
    throw "Package size $actualSizeMB MiB exceeds the configured limit of $MaxPackageSizeMB MiB."
}

Write-Host "Created $($file.FullName) ($($file.Length) bytes, $($entries.Count) entries)"
