$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location -LiteralPath $repoRoot
try {
    python -m tokentracker install @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

