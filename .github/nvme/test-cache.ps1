param([string]$CargoHome, [string]$TemporaryRoot)
$ErrorActionPreference = 'Stop'
$gitBin = 'C:\Program Files\Git\usr\bin'
New-Item -ItemType Directory -Path "$CargoHome\.nvme-probe" -Force | Out-Null
'cargo cache round-trip' | Set-Content "$CargoHome\.nvme-probe\entry.txt"
$archive = Join-Path $TemporaryRoot "cargo-smoke-$([guid]::NewGuid()).tar"
$restored = Join-Path $TemporaryRoot "nvme-restore-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path $restored | Out-Null
$paths = @{}
foreach ($entry in @{ cargo = $CargoHome; archive = $archive; restored = $restored }.GetEnumerator()) {
    $paths[$entry.Key] = & "$gitBin\cygpath.exe" -u $entry.Value
    if ($LASTEXITCODE -ne 0) { throw "Cannot normalize GNU tar path: $($entry.Value)" }
}
& "$gitBin\tar.exe" --force-local -cf $paths.archive -C $paths.cargo .nvme-probe
if ($LASTEXITCODE -ne 0) { throw 'GNU tar Cargo cache save failed' }
& "$gitBin\tar.exe" --force-local -xf $paths.archive -C $paths.restored
if ($LASTEXITCODE -ne 0) { throw 'GNU tar Cargo cache restore failed' }
if ((Get-FileHash "$CargoHome\.nvme-probe\entry.txt").Hash -ne
    (Get-FileHash "$restored\.nvme-probe\entry.txt").Hash) {
    throw 'Cargo cache round-trip content mismatch'
}
Write-Host 'GNU tar Cargo cache round-trip passed.'
