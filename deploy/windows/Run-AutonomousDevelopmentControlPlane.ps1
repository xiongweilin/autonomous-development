[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$required = @(
    "AUTODEV_DATABASE_URL",
    "AUTODEV_DBOS_SYSTEM_DATABASE_URL",
    "AUTODEV_STATE_ROOT",
)
foreach ($name in $required) {
    if (-not (Test-Path "Env:$name")) {
        throw "Required control-plane environment variable is missing: $name"
    }
}

$operatorSecret = Join-Path $env:ProgramData "AutonomousDevelopment\secrets\operator_hmac_secret"
if (-not (Test-Path -LiteralPath $operatorSecret -PathType Leaf)) {
    throw "Control-plane operator HMAC secret file is missing: $operatorSecret"
}
$env:AUTODEV_OPERATOR_HMAC_SECRET_FILE = $operatorSecret
$env:AUTODEV_API_HOST = "127.0.0.1"
$env:AUTODEV_API_PORT = "8765"

Set-Location $repoRoot
& uv run --project $repoRoot autonomous-development serve
exit $LASTEXITCODE
