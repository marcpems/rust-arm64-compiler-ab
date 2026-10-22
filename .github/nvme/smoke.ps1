param([ValidateSet('control', 'nvme')][string]$Variant)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\storage.ps1"
$workspace = $env:GITHUB_WORKSPACE
$original = @{ TEMP = $env:TEMP; TMP = $env:TMP; RUNNER_TEMP = $env:RUNNER_TEMP; GITHUB_WORKSPACE = $workspace }
$fixture = Join-Path $workspace '.nvme-fixture'
New-Item -ItemType Directory -Path "$fixture\empty" -Force | Out-Null
'preserve populated workspace' | Set-Content "$fixture\hidden.txt"
(Get-Item "$fixture\hidden.txt").Attributes = 'Hidden, ReadOnly'
$hash = (Get-FileHash -LiteralPath "$fixture\hidden.txt").Hash
New-Item -ItemType SymbolicLink -Path "$fixture\link.txt" -Target "$fixture\hidden.txt" | Out-Null
New-Item -ItemType Junction -Path "$fixture\junction" -Target $env:RUNNER_TEMP | Out-Null
$report = Invoke-NvmeSetup -Variant $Variant
$report | ConvertTo-Json -Depth 8 | Set-Content "$env:RUNNER_TEMP\nvme-setup.json"
if (($Variant -eq 'nvme' -and $report.status -ne 'applied') -or
    ($Variant -eq 'control' -and $report.status -ne 'control')) {
    throw "Runner does not support the intended comparison: $($report | ConvertTo-Json -Compress -Depth 6)"
}
Set-Location -LiteralPath $workspace
if ((Get-FileHash -LiteralPath "$fixture\hidden.txt").Hash -ne $hash -or
    (Get-Content -LiteralPath "$fixture\link.txt") -ne 'preserve populated workspace') {
    throw 'Relocation changed file or symbolic-link contents'
}
if (-not (Get-Item -LiteralPath "$fixture\hidden.txt" -Force).Attributes.HasFlag([IO.FileAttributes]::ReadOnly) -or
    -not (Get-Item -LiteralPath "$fixture\hidden.txt" -Force).Attributes.HasFlag([IO.FileAttributes]::Hidden) -or
    (Get-Item -LiteralPath "$fixture\junction").LinkType -ne 'Junction' -or
    -not (Test-Path -LiteralPath "$fixture\empty" -PathType Container)) {
    throw 'Relocation changed file attributes, junction or empty directory'
}
foreach ($key in $original.Keys) {
    if ([Environment]::GetEnvironmentVariable($key) -ne $original[$key]) {
        throw "Storage setup unexpectedly changed $key"
    }
}
if ($Variant -eq 'nvme') {
    if ((Get-FileHash -LiteralPath "$($report.workspace.backup)\.nvme-fixture\hidden.txt").Hash -ne $hash) {
        throw 'Original workspace backup was not preserved'
    }
    $again = Invoke-NvmeSetup -Variant nvme
    if ($again.status -ne 'skipped' -or $again.reason -ne 'existing-D') {
        throw 'Repeated setup did not leave the existing volume untouched'
    }
    Set-Location -LiteralPath $workspace
}
$cargo = if ($Variant -eq 'nvme') { 'D:\cargo-home' } elseif ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $HOME '.cargo' }
New-Item -ItemType Directory -Path "$cargo\.nvme-probe" -Force | Out-Null
'cargo cache round-trip' | Set-Content "$cargo\.nvme-probe\entry.txt"
$tar = 'C:\Program Files\Git\usr\bin\tar.exe'
& $tar -cf "$env:RUNNER_TEMP\cargo-smoke.tar" -C $cargo .nvme-probe
if ($LASTEXITCODE -ne 0) { throw 'GNU tar Cargo cache save failed' }
$restored = Join-Path $env:RUNNER_TEMP "nvme-restore-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path $restored | Out-Null
& $tar -xf "$env:RUNNER_TEMP\cargo-smoke.tar" -C $restored
if ($LASTEXITCODE -ne 0) { throw 'GNU tar Cargo cache restore failed' }
if ((Get-FileHash "$cargo\.nvme-probe\entry.txt").Hash -ne
    (Get-FileHash "$restored\.nvme-probe\entry.txt").Hash) {
    throw 'Cargo cache round-trip content mismatch'
}
$failureRoot = Join-Path $env:RUNNER_TEMP "nvme-failure-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path "$failureRoot\source", "$failureRoot\existing" | Out-Null
'preserved on failure' | Set-Content "$failureRoot\source\entry.txt"
$failed = $false
try { Move-VerifiedDirectory "$failureRoot\source" "$failureRoot\existing" | Out-Null }
catch {
    if ($_.Exception.Message -notlike 'Destination already exists:*') { throw }
    $failed = $true
}
if (-not $failed -or -not (Test-Path "$failureRoot\source\entry.txt")) {
    throw 'Destination conflict did not preserve original contents'
}
function New-Item { throw 'Injected junction failure' }
$failed = $false
try { Move-VerifiedDirectory "$failureRoot\source" "$failureRoot\copied" | Out-Null }
catch {
    if ($_.Exception.Message -ne 'Injected junction failure') { throw }
    $failed = $true
}
finally { Remove-Item Function:\New-Item }
$backups = @(Get-ChildItem -LiteralPath $failureRoot -Directory | Where-Object Name -like 'source.before-nvme-*')
if (-not $failed -or $backups.Count -ne 1 -or
    (Get-Content -LiteralPath "$($backups[0].FullName)\entry.txt") -ne 'preserved on failure') {
    throw 'Junction failure did not preserve the original backup'
}
Write-Host 'Workspace, attributes, links, backup, repeated setup, Cargo tar round-trip and failure preservation passed.'
