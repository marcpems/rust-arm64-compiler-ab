$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\storage.ps1"
$count = 0
function Check {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "Failed: $Name" }
    $script:count++
}
function Disk {
    return [pscustomobject]@{
        Number = 7; BusType = 'NVMe'; PartitionStyle = 'RAW'; Size = 220GB
        IsBoot = $false; IsSystem = $false; IsOffline = $false; IsReadOnly = $false
        NumberOfPartitions = 0
    }
}
$target = @{ OperatingSystem = 'Windows'; Architecture = 'ARM64'; Environment = 'github-hosted' }
Check ((Get-NvmeSelection @target -Disks @((Disk))).status -eq 'eligible') 'eligible disk'
Check ((Get-NvmeSelection @target).status -eq 'skipped') 'no disks'
Check ((Get-NvmeSelection @target -Disks @((Disk), (Disk))).status -eq 'skipped') 'ambiguous disks'
foreach ($change in @(
    @{ BusType = 'SAS' }, @{ PartitionStyle = 'GPT' }, @{ IsBoot = $true },
    @{ IsSystem = $true }, @{ IsOffline = $true }, @{ IsReadOnly = $true },
    @{ NumberOfPartitions = 1 }, @{ Size = 49GB }
)) {
    $disk = Disk
    foreach ($key in $change.Keys) { $disk.$key = $change[$key] }
    Check ((Get-NvmeSelection @target -Disks @($disk)).status -eq 'skipped') "unsafe disk $($change.Keys)"
}
Check ((Get-NvmeSelection @target -Disks @((Disk)) -Partitions @(
    [pscustomobject]@{ DiskNumber = 7; DriveLetter = 'X' }
)).status -eq 'skipped') 'partition inventory contradicts disk'
foreach ($parameter in @('Partitions', 'Volumes', 'Drives')) {
    $values = @{ Disks = @((Disk)) }
    $values[$parameter] = @([pscustomobject]@{ DriveLetter = 'D'; Name = 'D'; DiskNumber = 99 })
    Check ((Get-NvmeSelection @target @values).reason -eq 'existing-D') "existing D in $parameter"
}
foreach ($change in @(
    @{ OperatingSystem = 'Linux' }, @{ OperatingSystem = 'macOS' },
    @{ Architecture = 'X64' }, @{ Environment = 'self-hosted' }
)) {
    $arguments = $target.Clone()
    foreach ($key in $change.Keys) { $arguments[$key] = $change[$key] }
    Check ((Get-NvmeSelection @arguments -Disks @((Disk))).reason -eq 'non-target-runner') "excluded $($change.Values)"
}
Write-Host "$count storage-selection tests passed."
