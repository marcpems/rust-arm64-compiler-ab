param([Parameter(Mandatory)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
$release = Get-Content "$OutputDirectory\release.json" -Raw | ConvertFrom-Json
if ($release.timed_out -and $release.exit_code -eq 124) {
    Write-Host 'The measurement Job Object already terminated sccache; preserve its on-disk cache.'
} else {
    sccache --stop-server
    if ($LASTEXITCODE -ne 0) { throw "Cannot stop sccache before snapshot: $LASTEXITCODE" }
}
