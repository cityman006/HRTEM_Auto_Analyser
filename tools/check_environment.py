import argparse
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
p = argparse.ArgumentParser()
p.add_argument('--imports', action='store_true')
p.add_argument('--gui', action='store_true')
args = p.parse_args()
expected = ROOT / '.venv' / 'Scripts' / 'python.exe'
assert Path(sys.executable).resolve() == expected.resolve(), (sys.executable, str(expected))
versions = {}
for module, package in [('numpy','numpy'),('scipy','scipy'),('matplotlib','matplotlib'),('tifffile','tifffile'),('PIL','Pillow'),('imagecodecs','imagecodecs')]:
    importlib.import_module(module)
    versions[package] = importlib.metadata.version(package)
from packaging.requirements import Requirement
for line in (ROOT / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
    if not line.strip() or line.lstrip().startswith('#'):
        continue
    requirement = Requirement(line)
    installed = importlib.metadata.version(requirement.name)
    assert installed in requirement.specifier, (requirement.name, installed, str(requirement.specifier))
import tkinter as tk
import hrtem_spacing
import hrtem_gui
if args.gui:
    root = tk.Tk()
    root.update_idletasks()
    root.update()
    root.destroy()
    root = tk.Tk()
    app = hrtem_gui.HRTEMApp(root)
    root.update_idletasks()
    root.update()
    assert root.winfo_ismapped(), 'GUI did not map'
    assert 'HRTEM' in root.title()
    root.destroy()
print(json.dumps(dict(executable=sys.executable, python=sys.version, packages=versions, gui_instantiated=args.gui, passed=True), ensure_ascii=True))

