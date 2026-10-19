import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
results=[]
def run(path, args, cwd):
    start=time.monotonic()
    command=[os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/c',str(path),*args]
    proc=subprocess.run(command,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
    record=dict(command=command,cwd=str(cwd),exit_code=proc.returncode,seconds=time.monotonic()-start,stdout=proc.stdout,stderr=proc.stderr)
    results.append(record)
    with (ROOT/'logs'/'codex_repair.log').open('a',encoding='utf-8') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
    (ROOT/'logs'/'launcher_acceptance.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
    print(path.name, 'exit=',proc.returncode, 'seconds=',round(record['seconds'],2),flush=True)
    if proc.returncode:
        print(proc.stdout,proc.stderr)
        proc.check_returncode()
for name in ('START_HRTEM.cmd','REPAIR_HRTEM.cmd','01_INSTALL_AND_OPEN.cmd','02_OPEN_PROGRAM.cmd','03_CHECK_AND_REPAIR.cmd','05_PYTHON_DIAGNOSE.cmd'):
    run(ROOT/name,['-NonInteractive','-NoGui'],r'D:\CodexWork')
# Copy only launch/application sources; share the existing verified environment via a junction.
fixture=ROOT/'logs'/'launcher space 中文'
fixture.mkdir(exist_ok=False)
(fixture/'tools').mkdir()
for name in ('START_HRTEM.cmd','hrtem_spacing.py','hrtem_gui.py','requirements.txt'):
    shutil.copy2(ROOT/name,fixture/name)
for name in ('setup.ps1','probe_python.py','check_environment.py'):
    shutil.copy2(ROOT/'tools'/name,fixture/'tools'/name)
# No virtual environment is moved or copied.
junction=subprocess.run([os.environ.get('COMSPEC',r'C:\Windows\System32\cmd.exe'),'/d','/c','mklink','/J',str(fixture/'.venv'),str(ROOT/'.venv')],capture_output=True)
if junction.returncode: raise RuntimeError(repr(junction.stderr))
run(fixture/'START_HRTEM.cmd',['-NonInteractive','-NoGui'],r'D:\CodexWork')
print('ALL CMD ENTRY ACCEPTANCE PASSED',flush=True)
