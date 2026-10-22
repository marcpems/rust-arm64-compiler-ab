Set-StrictMode -Version Latest

function Get-NvmeSelection {
    param(
        [string]$OperatingSystem, [string]$Architecture, [string]$Environment,
        [object[]]$Disks = @(), [object[]]$Partitions = @(),
        [object[]]$Volumes = @(), [object[]]$Drives = @()
    )
    if ($OperatingSystem -ne 'Windows' -or $Architecture -ne 'ARM64' -or
        $Environment -ne 'github-hosted') {
        return @{ status = 'skipped'; reason = 'non-target-runner' }
    }
    if (@($Partitions | Where-Object DriveLetter -eq 'D').Count -or
        @($Volumes | Where-Object DriveLetter -eq 'D').Count -or
        @($Drives | Where-Object Name -eq 'D').Count) {
        return @{ status = 'skipped'; reason = 'existing-D' }
    }
    $partitioned = @($Partitions | ForEach-Object { $_.DiskNumber })
    $eligible = @($Disks | Where-Object {
        $_.BusType -eq 'NVMe' -and $_.PartitionStyle -eq 'RAW' -and
        -not $_.IsBoot -and -not $_.IsSystem -and
        -not $_.IsOffline -and -not $_.IsReadOnly -and
        $_.NumberOfPartitions -eq 0 -and $_.Size -ge 50GB -and
        $_.Number -notin $partitioned
    })
    if ($eligible.Count -ne 1) {
        return @{ status = 'skipped'; reason = 'missing-or-ambiguous-disk'; count = $eligible.Count }
    }
    return @{ status = 'eligible'; disk = $eligible[0] }
}

function Move-VerifiedDirectory {
    param([string]$Source, [string]$Destination)
    $sourceItem = Get-Item -LiteralPath $Source -Force
    if (-not $sourceItem.PSIsContainer -or
        ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        $sourceItem.FullName.TrimEnd('\') -eq [IO.Path]::GetPathRoot($sourceItem.FullName).TrimEnd('\')) {
        throw "Refusing relocation of nonordinary or root directory: $Source"
    }
    if (Test-Path -LiteralPath $Destination) { throw "Destination already exists: $Destination" }
    & robocopy $Source $Destination /E /COPY:DAT /DCOPY:DAT /SL /SJ /R:2 /W:1 /NP /NFL /NDL /NJH /NJS | Out-Host
    if ($LASTEXITCODE -ge 8) { throw "Directory copy failed: $Source (exit $LASTEXITCODE)" }
    & robocopy $Source $Destination /L /E /COPY:DAT /DCOPY:DAT /SL /SJ /R:0 /W:0 /NP /NFL /NDL /NJH /NJS | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Directory verification found differences: $Source (exit $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
    $backup = "$Source.before-nvme-$([guid]::NewGuid())"
    Move-Item -LiteralPath $Source -Destination $backup
    New-Item -ItemType Junction -Path $Source -Target $Destination | Out-Null
    return @{ source = $Source; destination = $Destination; backup = $backup }
}

function Invoke-NvmeSetup {
    param([ValidateSet('control', 'nvme')][string]$Variant)
    $watch = [Diagnostics.Stopwatch]::StartNew()
    if ($env:RUNNER_OS -ne 'Windows' -or $env:RUNNER_ARCH -ne 'ARM64' -or
        $env:HOST_ENVIRONMENT -ne 'github-hosted' -or $env:GITHUB_ACTIONS -ne 'true') {
        return @{ status = 'skipped'; reason = 'non-target-runner' }
    }
    if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'Arm64') {
        throw 'Runner and operating-system architectures differ'
    }
    $disks = @(Get-Disk)
    $partitions = @(Get-Partition)
    $volumes = @(Get-Volume)
    $drives = @(Get-PSDrive -PSProvider FileSystem)
    $selection = Get-NvmeSelection -OperatingSystem $env:RUNNER_OS -Architecture $env:RUNNER_ARCH `
        -Environment $env:HOST_ENVIRONMENT -Disks $disks -Partitions $partitions -Volumes $volumes -Drives $drives
    $report = [ordered]@{
        variant = $Variant
        image = $env:ImageOS
        imageVersion = $env:ImageVersion
        processors = [Environment]::ProcessorCount
        memory = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        disks = @($disks | Select-Object Number,FriendlyName,BusType,PartitionStyle,Size,NumberOfPartitions,IsBoot,IsSystem,IsOffline,IsReadOnly)
        selection = $selection.status
        originalTemp = $env:TEMP
        originalTmp = $env:TMP
        originalRunnerTemp = $env:RUNNER_TEMP
    }
    if ($selection.status -ne 'eligible') {
        $report.status = 'skipped'
        $report.reason = $selection.reason
        return $report
    }
    if ($Variant -eq 'control') {
        $report.status = 'control'
        $report.seconds = $watch.Elapsed.TotalSeconds
        return $report
    }
    $workspace = (Get-Item -LiteralPath $env:GITHUB_WORKSPACE -Force)
    $cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $HOME '.cargo' }
    foreach ($path in @($workspace.FullName, $cargoHome)) {
        if (Test-Path -LiteralPath $path) {
            $entry = Get-Item -LiteralPath $path -Force
            if (-not $entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Refusing nonordinary relocation source: $path"
            }
        }
    }
    $selectedNumber = $selection.disk.Number
    $fresh = Get-NvmeSelection -OperatingSystem $env:RUNNER_OS -Architecture $env:RUNNER_ARCH `
        -Environment $env:HOST_ENVIRONMENT -Disks @(Get-Disk) -Partitions @(Get-Partition) `
        -Volumes @(Get-Volume) -Drives @(Get-PSDrive -PSProvider FileSystem)
    if ($fresh.status -ne 'eligible' -or $fresh.disk.Number -ne $selectedNumber) {
        throw 'Disk inventory changed before initialization'
    }
    $partition = Initialize-Disk -InputObject $fresh.disk -PartitionStyle GPT -PassThru |
        New-Partition -UseMaximumSize -DriveLetter D
    $volume = $partition | Format-Volume -DevDrive -FileSystem ReFS -NewFileSystemLabel 'Build' -Confirm:$false
    if ($volume.FileSystem -ne 'ReFS' -or $volume.DriveLetter -ne 'D') {
        throw 'New build volume is not ReFS on D:'
    }
    Set-Location -LiteralPath $env:RUNNER_TEMP
    [Environment]::CurrentDirectory = $env:RUNNER_TEMP
    $report.workspace = Move-VerifiedDirectory -Source $workspace.FullName -Destination 'D:\workspace'
    if (Test-Path -LiteralPath $cargoHome) {
        $report.cargo = Move-VerifiedDirectory -Source $cargoHome -Destination 'D:\cargo-home'
    } else {
        New-Item -ItemType Directory -Path 'D:\cargo-home' | Out-Null
        New-Item -ItemType Junction -Path $cargoHome -Target 'D:\cargo-home' | Out-Null
    }
    "CARGO_HOME=D:\cargo-home" >> $env:GITHUB_ENV
    $report.status = 'applied'
    $report.seconds = $watch.Elapsed.TotalSeconds
    return $report
}
