param(
    [ValidateSet('install','run','repair','diagnose')][string]$Mode='run',
    [string]$BaseDir='',
    [switch]$NoGui,
    [switch]$NonInteractive
)
$ErrorActionPreference='Stop'
if (-not $BaseDir) { $BaseDir=Split-Path -Parent $PSScriptRoot }
if ($NonInteractive) { $NoGui=$true }
$Root=(Resolve-Path -LiteralPath $BaseDir).Path
$Logs=Join-Path $Root 'logs'
[void](New-Item -ItemType Directory -Path $Logs -Force)
$Utf8=New-Object System.Text.UTF8Encoding($false)
$Venv=Join-Path $Root '.venv'
$Python=Join-Path $Venv 'Scripts\python.exe'
$Probe=Join-Path $PSScriptRoot 'probe_python.py'
$Check=Join-Path $PSScriptRoot 'check_environment.py'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONNOUSERSITE='1'
$env:MPLCONFIGDIR=Join-Path $Root '.cache\matplotlib'
function Log([string]$Message) {
    $line=(Get-Date -Format o)+' '+$Message
    Write-Host $Message
    [IO.File]::AppendAllText((Join-Path $Logs 'codex_repair.log'),$line+[Environment]::NewLine,$Utf8)
    [IO.File]::AppendAllText((Join-Path $Logs 'setup.log'),$line+[Environment]::NewLine,$Utf8)
}
# Windows CommandLineToArgvW quoting, including terminal backslashes.
function Quote-Argument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s" ]') { return $Value }
    return '"'+[regex]::Replace([regex]::Replace($Value,'(\\*)"','$1$1\"'),'(\\+)$','$1$1')+'"'
}
function Invoke-Recorded([string]$Exe,[string[]]$Arguments,[int]$Timeout=120,[switch]$AllowFailure) {
    $command=(Quote-Argument $Exe)+' '+(($Arguments | ForEach-Object {Quote-Argument $_}) -join ' ')
    Log ('RUN '+$command)
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Exe
    $info.Arguments=($Arguments | ForEach-Object {Quote-Argument $_}) -join ' '
    $info.WorkingDirectory=$Root
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $info.StandardOutputEncoding=$Utf8
    $info.StandardErrorEncoding=$Utf8
    $proc=New-Object Diagnostics.Process
    $proc.StartInfo=$info
    $out=''; $err=''; $code=-1
    try {
        [void]$proc.Start()
        $readOut=$proc.StandardOutput.ReadToEndAsync()
        $readErr=$proc.StandardError.ReadToEndAsync()
        if(-not $proc.WaitForExit($Timeout*1000)) {
            $proc.Kill(); $proc.WaitForExit(); $err='TIMEOUT after '+$Timeout+' seconds. '
        }
        $out=$readOut.GetAwaiter().GetResult()
        $err+=$readErr.GetAwaiter().GetResult()
        $code=$proc.ExitCode
    } catch { $err+=$_.Exception.ToString() } finally { $proc.Dispose() }
    $result=[pscustomobject]@{command=$command;stdout=$out;stderr=$err;exit_code=$code}
    Log ('RESULT '+($result | ConvertTo-Json -Compress))
    if($code -ne 0 -and -not $AllowFailure){throw "Command failed ($code): $command`n$err"}
    return $result
}
function Test-Python([string]$Exe,[string[]]$Prefix=@()) {
    if($Exe -match '\\WindowsApps\\') { Log ('SKIP execution alias '+$Exe); return $null }
    $r=Invoke-Recorded $Exe (@($Prefix)+@($Probe)) 20 -AllowFailure
    $reason=''; $data=$null
    if($r.exit_code -eq 0) {
        try {
            $data=$r.stdout | ConvertFrom-Json
            if($data.bits -ne 64 -or -not $data.venv -or -not $data.ensurepip -or -not $data.tkinter) { $reason='Requires 64-bit interpreter with venv, ensurepip and tkinter'; $data=$null }
        } catch { $reason='Invalid probe JSON: '+$_.Exception.Message }
    } else { $reason='Probe execution failed' }
    [IO.File]::AppendAllText((Join-Path $Logs 'python_detection.log'),(@{time=(Get-Date -Format o);result=$r;reason=$reason;probe=$data} | ConvertTo-Json -Depth 6 -Compress)+[Environment]::NewLine,$Utf8)
    return $data
}
function Find-Python {
    $candidates=New-Object 'System.Collections.Generic.List[string]'
    foreach($base in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'))) {
        foreach($version in @('311','312','313')) { $candidates.Add((Join-Path $base ('Python'+$version+'\python.exe'))) }
    }
    $launcher=Get-Command py.exe -ErrorAction SilentlyContinue
    if($launcher) {
        $inventory=Invoke-Recorded $launcher.Source @('-0p') 20 -AllowFailure
        foreach($line in (($inventory.stdout+"`n"+$inventory.stderr) -split "`n")) {
            if($line -match '([A-Za-z]:\\.+?python.exe)\s*$'){$candidates.Add($Matches[1])}
        }
    }
    foreach($key in @('HKCU:\Software\Python\*\*\InstallPath','HKLM:\Software\Python\*\*\InstallPath','HKLM:\Software\WOW6432Node\Python\*\*\InstallPath')) {
        foreach($item in @(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue)) {
            if($item.ExecutablePath){$candidates.Add($item.ExecutablePath)}
            elseif($item.'(default)'){$candidates.Add((Join-Path $item.'(default)' 'python.exe'))}
        }
    }
    foreach($item in @(Get-Command python.exe,python3.exe -All -ErrorAction SilentlyContinue)){$candidates.Add($item.Source)}
    foreach($pattern in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\*\python.exe'),'C:\Program Files\Python*\python.exe','C:\Python*\python.exe')) {
        foreach($item in @(Get-Item -Path $pattern -ErrorAction SilentlyContinue)){$candidates.Add($item.FullName)}
    }
    foreach($exe in @($candidates | Select-Object -Unique)) {
        if(Test-Path -LiteralPath $exe){$data=Test-Python $exe; if($null -ne $data){return $data}}
    }
    throw 'No verified complete x64 Python found. See logs\python_detection.log. Existing interpreters were not modified; no repeated WinGet installation was attempted.'
}
try {
    Log ('HRTEM mode='+$Mode+' root='+$Root+' PowerShell='+$PSVersionTable.PSVersion)
    $data=$null
    if(Test-Path -LiteralPath $Python){$data=Test-Python $Python}
    if($Mode -eq 'diagnose') {
        if($null -eq $data){$data=Find-Python}
        [IO.File]::WriteAllText((Join-Path $Logs 'diagnostics.log'),($data | ConvertTo-Json -Depth 5),$Utf8)
        exit 0
    }
    if($null -eq $data) {
        $base=Find-Python
        if(Test-Path -LiteralPath $Venv) {
            $resolved=[IO.Path]::GetFullPath($Venv)
            if($resolved -ne (Join-Path $Root '.venv')){throw 'Unexpected venv path'}
            $preserved=Join-Path $Root ('.venv.backup_'+(Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
            Move-Item -LiteralPath $resolved -Destination $preserved
            Log ('Preserved old environment at '+$preserved)
        }
        $null=Invoke-Recorded $base.executable @('-m','venv',$Venv) 180
    }
    $health=Invoke-Recorded $Python @($Check,'--imports') 90 -AllowFailure
    $pip=Invoke-Recorded $Python @('-m','pip','--disable-pip-version-check','check') 90 -AllowFailure
    if($health.exit_code -ne 0 -or $pip.exit_code -ne 0) {
        $requirements=Join-Path $Root 'requirements.txt'
        if(Test-Path -LiteralPath (Join-Path $Root 'requirements.lock.txt')){$requirements=Join-Path $Root 'requirements.lock.txt'}
        $pipArguments=@('-m','pip','--isolated','install','--disable-pip-version-check','--only-binary=:all:','--force-reinstall','--retries','2','--timeout','40','-r',$requirements)
        $wheelDir=Join-Path $Root '.downloads\wheels'
        $cacheOK=$false
        if(Test-Path -LiteralPath (Join-Path $wheelDir 'official_manifest.json')) {
            $verified=Invoke-Recorded $Python @((Join-Path $PSScriptRoot 'verify_wheels.py')) 90 -AllowFailure
            $cacheOK=$verified.exit_code -eq 0
        }
        if($cacheOK) {
            Log 'Using SHA256-verified local official wheels for offline repair.'
            $installed=Invoke-Recorded $Python ($pipArguments+@('--no-index','--find-links',$wheelDir)) 900 -AllowFailure
            if($installed.exit_code -ne 0){$cacheOK=$false}
        }
        if(-not $cacheOK) {
            $null=Invoke-Recorded $Python ($pipArguments+@('--index-url','https://pypi.org/simple')) 900
        }
        $null=Invoke-Recorded $Python @($Check,'--imports') 90
        $null=Invoke-Recorded $Python @('-m','pip','check') 90
    }
    if($Mode -ne 'run' -or $NoGui) {
        $null=Invoke-Recorded $Python @($Check,'--gui') 90
        $null=Invoke-Recorded $Python @((Join-Path $Root 'hrtem_spacing.py'),'--self-test') 120
        $frozen=Invoke-Recorded $Python @('-m','pip','freeze') 90
        [IO.File]::WriteAllText((Join-Path $Root 'requirements.lock.txt'),$frozen.stdout,$Utf8)
    }
    $data=Test-Python $Python
    [IO.File]::WriteAllText((Join-Path $Logs 'diagnostics.log'),($data | ConvertTo-Json -Depth 5),$Utf8)
    if(-not $NoGui) {
        Log 'Opening HRTEM main window using the project interpreter.'
        # Real interactive GUI intentionally has no automatic timeout.
        & $Python (Join-Path $Root 'hrtem_gui.py')
        if($LASTEXITCODE -ne 0){throw ('GUI exited with '+$LASTEXITCODE)}
    }
    Log 'HRTEM completed successfully.'
    exit 0
} catch {
    Log ('ERROR '+$_.Exception.ToString())
    Log 'Repair details: logs\codex_repair.log and logs\python_detection.log'
    exit 1
}



