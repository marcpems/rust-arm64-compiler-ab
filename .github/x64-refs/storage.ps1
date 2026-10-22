Set-StrictMode -Version Latest
. "$PSScriptRoot\..\nvme\storage.ps1"

function Get-X64StoragePlan {
    param(
        [string]$OperatingSystem, [string]$Architecture, [string]$Environment,
        [object[]]$Disks, [object[]]$Partitions, [object[]]$Volumes,
        [object[]]$Drives, [long]$SupportedMinimum
    )
    if ($OperatingSystem -ne 'Windows' -or $Architecture -ne 'X64' -or
        $Environment -ne 'github-hosted') { throw 'Non-target runner' }
    if (@($Partitions | Where-Object DriveLetter -eq 'R').Count -or
        @($Volumes | Where-Object DriveLetter -eq 'R').Count -or
        @($Drives | Where-Object Name -eq 'R').Count) { throw 'R is occupied' }
    $data = @($Partitions | Where-Object DriveLetter -eq 'D')
    $volume = @($Volumes | Where-Object DriveLetter -eq 'D')
    if ($data.Count -ne 1 -or $volume.Count -ne 1) { throw 'Ambiguous D volume' }
    $data = $data[0]
    $volume = $volume[0]
    $disk = @($Disks | Where-Object Number -eq $data.DiskNumber)
    if ($disk.Count -ne 1) { throw 'Ambiguous D disk' }
    $disk = $disk[0]
    if ($disk.IsBoot -or $disk.IsSystem -or $disk.IsOffline -or $disk.IsReadOnly -or
        $data.IsBoot -or $data.IsSystem -or $disk.BusType -notin @('NVMe', 'SAS') -or
        $disk.PartitionStyle -notin @('MBR', 'GPT') -or
        $volume.FileSystem -ne 'NTFS' -or $volume.HealthStatus -ne 'Healthy' -or
        $volume.DriveType -ne 'Fixed') { throw 'Unsafe D disk or filesystem' }
    $other = @($Partitions | Where-Object {
        $_.DiskNumber -eq $disk.Number -and $_.PartitionNumber -ne $data.PartitionNumber
    })
    if ($other.Count -and -not (
        $disk.PartitionStyle -eq 'GPT' -and $other.Count -eq 1 -and
        $other[0].Type -eq 'Reserved' -and -not $other[0].IsBoot -and -not $other[0].IsSystem -and
        [string]$other[0].DriveLetter -notmatch '[A-Za-z]' -and
        $other[0].Offset + $other[0].Size -le $data.Offset
    )) { throw 'Unsupported partition layout' }
    if ($SupportedMinimum -le 0 -or $SupportedMinimum -ge $data.Size) {
        throw 'Invalid supported shrink limit'
    }
    $used = $volume.Size - $volume.SizeRemaining
    $retain = [long]([Math]::Ceiling(
        [Math]::Max(32GB, [Math]::Max($SupportedMinimum, $used + 16GB)) / 1MB
    ) * 1MB)
    if ($data.Size - $retain -lt 80GB) { throw 'Insufficient ReFS capacity with NTFS reserve' }
    return @{
        diskNumber = $disk.Number; partitionNumber = $data.PartitionNumber
        originalSize = $data.Size; supportedMinimum = $SupportedMinimum
        retainedSize = $retain; bus = [string]$disk.BusType; diskSize = $disk.Size
        partitionStyle = [string]$disk.PartitionStyle
    }
}

function Get-X64PagingState {
    @{
        automatic = (Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile
        settings = @(Get-CimInstance Win32_PageFileSetting | Select-Object Name,InitialSize,MaximumSize)
    } | ConvertTo-Json -Depth 4 -Compress
}

function Invoke-X64Storage {
    param([ValidateSet('control', 'refs')][string]$Variant)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    if ($env:GITHUB_ACTIONS -ne 'true' -or $env:HOST_ENVIRONMENT -ne 'github-hosted' -or
        $env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'X64' -or
        [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
        throw 'Storage mutation is restricted to hosted Windows x64'
    }
    $disks = @(Get-Disk)
    $partitions = @(Get-Partition)
    $volumes = @(Get-Volume)
    $minimum = (Get-Partition -DriveLetter D | Get-PartitionSupportedSize).SizeMin
    $plan = Get-X64StoragePlan -OperatingSystem $env:RUNNER_OS -Architecture $env:RUNNER_ARCH `
        -Environment $env:HOST_ENVIRONMENT -Disks $disks -Partitions $partitions `
        -Volumes $volumes -Drives @(Get-PSDrive -PSProvider FileSystem) -SupportedMinimum $minimum
    $paging = Get-X64PagingState
    $report = [ordered]@{
        variant = $Variant; plan = $plan; image = $env:ImageOS; imageVersion = $env:ImageVersion
        cpu = @((Get-CimInstance Win32_Processor).Name); processors = [Environment]::ProcessorCount
        memory = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        pagingBefore = $paging
        defender = Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AMProductVersion
        disks = @($disks | Select-Object Number,BusType,Size,PartitionStyle,IsBoot,IsSystem)
        partitionsBefore = @($partitions | Select-Object DiskNumber,PartitionNumber,DriveLetter,Size,Offset,Type)
        volumesBefore = @($volumes | Select-Object DriveLetter,FileSystem,Size,SizeRemaining)
        tempBefore = $env:TEMP; tmpBefore = $env:TMP; runnerTemp = $env:RUNNER_TEMP
    }
    if ($Variant -eq 'refs') {
        $cargo = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $HOME '.cargo' }
        if (-not (Test-Path -LiteralPath $cargo)) { New-Item -ItemType Directory $cargo | Out-Null }
        $moves = @(
            @{ source = $env:RUNNER_TEMP; destination = 'R:\runner-temp' }
            @{ source = $env:GITHUB_WORKSPACE; destination = 'R:\workspace' }
            @{ source = $cargo; destination = 'R:\cargo-home' }
        )
        foreach ($move in $moves) {
            $item = Get-Item -LiteralPath $move.source -Force
            if (-not $item.PSIsContainer -or $null -eq $item.Parent -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Unsafe relocation source: $($move.source)"
            }
            $move.source = $item.FullName.TrimEnd('\')
        }
        foreach ($left in $moves) {
            foreach ($right in $moves) {
                if ($left.destination -ne $right.destination -and (
                    $left.source -eq $right.source -or
                    $left.source.StartsWith($right.source + '\', [StringComparison]::OrdinalIgnoreCase))) {
                    throw 'Overlapping relocation sources'
                }
            }
        }
        if (-not $env:GITHUB_WORKSPACE.StartsWith('D:\', [StringComparison]::OrdinalIgnoreCase) -or
            -not $env:RUNNER_TEMP.StartsWith('D:\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Expected workspace and runner temporary directory on D'
        }
        $fresh = Get-X64StoragePlan -OperatingSystem $env:RUNNER_OS -Architecture $env:RUNNER_ARCH `
            -Environment $env:HOST_ENVIRONMENT -Disks @(Get-Disk) -Partitions @(Get-Partition) `
            -Volumes @(Get-Volume) -Drives @(Get-PSDrive -PSProvider FileSystem) `
            -SupportedMinimum (Get-Partition -DriveLetter D | Get-PartitionSupportedSize).SizeMin
        foreach ($field in @('diskNumber', 'partitionNumber', 'originalSize', 'retainedSize')) {
            if ($fresh[$field] -ne $plan[$field]) { throw 'Disk inventory changed before resizing' }
        }
        Resize-Partition -DiskNumber $plan.diskNumber -PartitionNumber $plan.partitionNumber -Size $plan.retainedSize
        $new = New-Partition -DiskNumber $plan.diskNumber -UseMaximumSize -DriveLetter R
        $volume = $new | Format-Volume -FileSystem ReFS -NewFileSystemLabel 'RustExperiment' -Confirm:$false
        if ($volume.FileSystem -ne 'ReFS' -or $volume.DriveLetter -ne 'R') { throw 'Incorrect new volume' }
        Set-Location -LiteralPath $env:SystemRoot
        [Environment]::CurrentDirectory = $env:SystemRoot
        $report.relocations = @($moves | ForEach-Object {
            Move-VerifiedDirectory -Source $_.source -Destination $_.destination
        })
        New-Item -ItemType Directory 'R:\build-temp' | Out-Null
        "CARGO_HOME=R:\cargo-home" >> $env:GITHUB_ENV
        "TEMP=R:\build-temp" >> $env:GITHUB_ENV
        "TMP=R:\build-temp" >> $env:GITHUB_ENV
        'X64_REFS_FILE_COMMAND_OK=true' >> $env:GITHUB_ENV
        if ((Get-Partition -DriveLetter D).Size -ne $plan.retainedSize -or
            (Get-Volume -DriveLetter D).FileSystem -ne 'NTFS') { throw 'NTFS preservation failed' }
    }
    $report.pagingAfter = Get-X64PagingState
    if ($report.pagingAfter -ne $paging) { throw 'Paging policy changed' }
    $report.volumesAfter = @(Get-Volume | Select-Object DriveLetter,FileSystem,Size,SizeRemaining)
    $report.seconds = $watch.Elapsed.TotalSeconds
    $report.status = if ($Variant -eq 'refs') { 'applied' } else { 'control' }
    return $report
}
