param([ValidateSet('control', 'refs')][string]$Variant)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\storage.ps1"
$workspace = $env:GITHUB_WORKSPACE
$fixture = Join-Path $workspace '.refs-fixture'
New-Item -ItemType Directory "$fixture\empty" | Out-Null
'preserve workspace' | Set-Content "$fixture\hidden.txt"
(Get-Item "$fixture\hidden.txt").Attributes = 'Hidden, ReadOnly'
New-Item -ItemType SymbolicLink "$fixture\link" -Target "$fixture\hidden.txt" | Out-Null
New-Item -ItemType Junction "$fixture\junction" -Target $env:RUNNER_TEMP | Out-Null
$hash = (Get-FileHash "$fixture\hidden.txt").Hash
$report = Invoke-X64Storage -Variant $Variant
$report | ConvertTo-Json -Depth 10 | Set-Content "$env:RUNNER_TEMP\x64-storage.json"
Set-Location -LiteralPath $workspace
if ((Get-FileHash "$fixture\hidden.txt").Hash -ne $hash -or
    (Get-Content "$fixture\link") -ne 'preserve workspace' -or
    (Get-Item "$fixture\junction").LinkType -ne 'Junction' -or
    -not (Get-Item "$fixture\hidden.txt" -Force).Attributes.HasFlag([IO.FileAttributes]::Hidden) -or
    -not (Get-Item "$fixture\hidden.txt" -Force).Attributes.HasFlag([IO.FileAttributes]::ReadOnly) -or
    -not (Test-Path "$fixture\empty" -PathType Container)) { throw 'Fixture preservation failed' }
if ($Variant -eq 'refs') {
    $backup = @($report.relocations | Where-Object destination -eq 'R:\workspace')[0].backup
    if ((Get-FileHash "$backup\.refs-fixture\hidden.txt").Hash -ne $hash) { throw 'Backup missing' }
    $rejected = $false
    try { Invoke-X64Storage -Variant refs | Out-Null }
    catch {
        if ($_.Exception.Message -ne 'R is occupied') { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw 'Repeated storage setup was not rejected' }
}
$cargo = if ($Variant -eq 'refs') { 'R:\cargo-home' } elseif ($env:CARGO_HOME) { $env:CARGO_HOME } else { "$HOME\.cargo" }
& "$PSScriptRoot\..\nvme\test-cache.ps1" -CargoHome $cargo -TemporaryRoot $env:RUNNER_TEMP
$path = Join-Path $env:RUNNER_TEMP 'refs-write-check.bin'
$stream = [IO.File]::Create($path)
try {
    $buffer = [byte[]]::new(1MB)
    for ($i = 0; $i -lt 512; $i++) { $stream.Write($buffer) }
    $stream.Flush($true)
} finally { $stream.Dispose() }
if ((Get-Item $path).Length -ne 512MB) { throw 'Runner temporary write failed' }
Remove-Item -LiteralPath $path
'X64_SAFETY_FILE_COMMAND_OK=true' >> $env:GITHUB_ENV
