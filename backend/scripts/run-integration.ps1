param(
    [Parameter(Mandatory = $true)]
    [string]$EnvFile,

    [string]$Python = ".\.venv\Scripts\python.exe",

    [string]$TestPath = "backend\tests\integration",

    [switch]$Coverage
)

$ErrorActionPreference = "Stop"
$values = @{}

foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
        continue
    }
    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
        continue
    }
    $values[$trimmed.Substring(0, $separator)] = $trimmed.Substring($separator + 1)
}

$required = @("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_PORT", "REDIS_PASSWORD", "REDIS_PORT")
foreach ($name in $required) {
    if (-not $values.ContainsKey($name) -or -not $values[$name]) {
        throw "Missing required local integration setting: $name"
    }
}

$databaseUser = [uri]::EscapeDataString($values["POSTGRES_USER"])
$databasePassword = [uri]::EscapeDataString($values["POSTGRES_PASSWORD"])
$redisPassword = [uri]::EscapeDataString($values["REDIS_PASSWORD"])

$env:WHEELMATCH_TEST_DATABASE_URL = "postgresql+asyncpg://${databaseUser}:${databasePassword}@127.0.0.1:$($values['POSTGRES_PORT'])/$($values['POSTGRES_DB'])"
$env:WHEELMATCH_TEST_REDIS_URL = "redis://:${redisPassword}@127.0.0.1:$($values['REDIS_PORT'])/0"

if ($Coverage) {
    $coverageConfig = Join-Path (Split-Path -Parent $PSScriptRoot) "pyproject.toml"
    & $Python -m pytest $TestPath --cov=app --cov-config=$coverageConfig --cov-report=term-missing
}
else {
    & $Python -m pytest $TestPath
}
exit $LASTEXITCODE
