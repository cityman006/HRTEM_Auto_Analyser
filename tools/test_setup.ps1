$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $PSScriptRoot
$Logs=Join-Path $Root 'logs'
$Utf8=New-Object System.Text.UTF8Encoding($false)
$Probe=Join-Path $PSScriptRoot 'probe_python.py'
$Python=Join-Path $Root '.venv\Scripts\python.exe'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'setup.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors | Out-String)}
foreach($function in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$false)) { Invoke-Expression $function.Extent.Text }
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
$found=Find-Python
if(-not $found.executable){throw 'Discovery failed'}
$fixture=Join-Path $Logs 'path with spaces 中文'
[void](New-Item -ItemType Directory -Path $fixture -Force)
$echo=Join-Path $fixture 'echo args.py'
[IO.File]::WriteAllText($echo,"import json, sys`nprint(json.dumps(sys.argv[1:], ensure_ascii=True))`n",$Utf8)
$expected=@('C:\test folder\中文\','embedded"quote','plain')
$r=Invoke-Recorded $Python (@($echo)+$expected) 20
$actual=$r.stdout | ConvertFrom-Json
for($i=0;$i -lt $expected.Count;$i++){if($actual[$i] -cne $expected[$i]){throw "Argument roundtrip failed at $i"}}
$bad=Invoke-Recorded $Python @((Join-Path $fixture 'missing.py')) 20 -AllowFailure
if($bad.exit_code -eq 0 -or -not $bad.stderr){throw 'Failure logging not verified'}
$delay=Join-Path $fixture 'timeout.py'
[IO.File]::WriteAllText($delay,"import time`ntime.sleep(10)`n",$Utf8)
$timed=Invoke-Recorded $Python @($delay) 1 -AllowFailure
if($timed.exit_code -eq 0 -or $timed.stderr -notmatch 'TIMEOUT'){throw 'Timeout not verified'}
Log 'REGRESSION PASSED: Python discovery, PS5.1 parse, spaces/Chinese/trailing slash/embedded quote arguments, separate stderr/nonzero exit, process timeout.'

