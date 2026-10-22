$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\storage.ps1"
function Fixture {
    @{
        OperatingSystem = 'Windows'; Architecture = 'X64'; Environment = 'github-hosted'
        Disks = @(@{ Number = 0; BusType = 'NVMe'; PartitionStyle = 'GPT'; Size = 220GB
            IsBoot = $false; IsSystem = $false; IsOffline = $false; IsReadOnly = $false })
        Partitions = @(
            @{ DiskNumber = 0; PartitionNumber = 1; DriveLetter = ''; Type = 'Reserved'
                IsBoot = $false; IsSystem = $false; Offset = 1MB; Size = 16MB }
            @{ DiskNumber = 0; PartitionNumber = 2; DriveLetter = 'D'; Type = 'Basic'
                IsBoot = $false; IsSystem = $false; Offset = 17MB; Size = 219GB }
        )
        Volumes = @(@{ DriveLetter = 'D'; FileSystem = 'NTFS'; HealthStatus = 'Healthy'
            DriveType = 'Fixed'; Size = 219GB; SizeRemaining = 216GB })
        Drives = @(@{ Name = 'C' }, @{ Name = 'D' })
        SupportedMinimum = 3GB
    }
}
$f = Fixture
$plan = Get-X64StoragePlan @f
if ($plan.retainedSize -ne 32GB) { throw 'NTFS reserve missing' }
$f.Volumes[0].SizeRemaining = 170GB
if ((Get-X64StoragePlan @f).retainedSize -lt 65GB) { throw 'Used-space reserve missing' }
$f = Fixture
$f.Disks[0].BusType = 'SAS'
$f.Disks[0].PartitionStyle = 'MBR'
$f.Partitions = @($f.Partitions[1])
Get-X64StoragePlan @f | Out-Null
$cases = @(
    @{ name = 'architecture'; change = { param($f) $f.Architecture = 'ARM64' }; error = 'Non-target*' }
    @{ name = 'environment'; change = { param($f) $f.Environment = 'self-hosted' }; error = 'Non-target*' }
    @{ name = 'OS'; change = { param($f) $f.OperatingSystem = 'Linux' }; error = 'Non-target*' }
    @{ name = 'R partition'; change = { param($f) $f.Partitions[1].DriveLetter = 'R' }; error = 'R is occupied' }
    @{ name = 'R volume'; change = { param($f) $f.Volumes += @{ DriveLetter = 'R' } }; error = 'R is occupied' }
    @{ name = 'R drive'; change = { param($f) $f.Drives += @{ Name = 'R' } }; error = 'R is occupied' }
    @{ name = 'boot disk'; change = { param($f) $f.Disks[0].IsBoot = $true }; error = 'Unsafe*' }
    @{ name = 'system disk'; change = { param($f) $f.Disks[0].IsSystem = $true }; error = 'Unsafe*' }
    @{ name = 'offline'; change = { param($f) $f.Disks[0].IsOffline = $true }; error = 'Unsafe*' }
    @{ name = 'read-only'; change = { param($f) $f.Disks[0].IsReadOnly = $true }; error = 'Unsafe*' }
    @{ name = 'boot partition'; change = { param($f) $f.Partitions[1].IsBoot = $true }; error = 'Unsafe*' }
    @{ name = 'filesystem'; change = { param($f) $f.Volumes[0].FileSystem = 'ReFS' }; error = 'Unsafe*' }
    @{ name = 'health'; change = { param($f) $f.Volumes[0].HealthStatus = 'Warning' }; error = 'Unsafe*' }
    @{ name = 'unknown bus'; change = { param($f) $f.Disks[0].BusType = 'USB' }; error = 'Unsafe*' }
    @{ name = 'extra data'; change = { param($f) $f.Partitions[0].Type = 'Basic' }; error = 'Unsupported*' }
    @{ name = 'trailing reserved'; change = { param($f) $f.Partitions[0].Offset = 220GB }; error = 'Unsupported*' }
    @{ name = 'reserved system'; change = { param($f) $f.Partitions[0].IsSystem = $true }; error = 'Unsupported*' }
    @{ name = 'missing D'; change = { param($f) $f.Partitions = @() }; error = 'Ambiguous*' }
    @{ name = 'ambiguous D'; change = { param($f) $f.Partitions += $f.Partitions[1] }; error = 'Ambiguous*' }
    @{ name = 'invalid minimum'; change = { param($f) $f.SupportedMinimum = 0 }; error = 'Invalid*' }
    @{ name = 'insufficient space'; change = { param($f) $f.SupportedMinimum = 180GB }; error = 'Insufficient*' }
)
foreach ($case in $cases) {
    $f = Fixture
    & $case.change $f
    $rejected = $false
    try { Get-X64StoragePlan @f | Out-Null }
    catch {
        if ($_.Exception.Message -notlike $case.error) { throw }
        $rejected = $true
    }
    if (-not $rejected) { throw "Unsafe case accepted: $($case.name)" }
}
Write-Host "Passed 3 capacity/layout cases and $($cases.Count) rejection cases."
