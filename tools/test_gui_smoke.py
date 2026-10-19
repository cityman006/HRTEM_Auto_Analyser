import json
import sys
from pathlib import Path
import tkinter as tk
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hrtem_gui import HRTEMApp
root = tk.Tk()
root.update()
root.destroy()
root = tk.Tk()
app = HRTEMApp(root)
root.update_idletasks()
root.update()
assert root.winfo_ismapped()
print(json.dumps(dict(tk=tk.TkVersion, title=root.title(), geometry=root.geometry(), mapped=bool(root.winfo_ismapped()), executable=sys.executable), ensure_ascii=True))
root.destroy()
