#!/usr/bin/env python3
"""Create the full Tk GUI and verify the extension controls exist."""
from pathlib import Path
import sys
import tkinter as tk
import numpy as np
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hrtem_gui import HRTEMApp
from saed_analysis import analyze_saed
from saed_editor import open_saed_editor

root = tk.Tk()
root.withdraw()
app = HRTEMApp(root)
root.update_idletasks()
tabs = [app.notebook.tab(tab, 'text') for tab in app.notebook.tabs()]
assert tabs == ['HRTEM', 'SAED', 'Export Settings'], tabs
assert app.run_button.cget('text') == '▶ 原有 HRTEM 分析'
assert 'IFFT' in app.roi_button.cget('text')
assert '自动识别' in app.saed_button.cget('text')
assert app.last_hrtem_output is None and app.last_saed_output is None and app.last_roi_output is None
assert str(app.status_link.cget('cursor')) == 'hand2'
app._set_result_link('hrtem', ROOT)
app._set_result_link('saed', ROOT)
assert app.hrtem_result_target == ROOT.resolve()
assert app.saed_result_target == ROOT.resolve()
assert app.hrtem_result_link.get().startswith('打开 HRTEM 结果文件夹：')
assert app.saed_result_link.get().startswith('打开 SAED 结果文件夹：')
with patch('hrtem_gui.os.startfile') as opened:
    app.open_result_folder('hrtem'); opened.assert_called_once_with(str(ROOT.resolve()))
with patch('hrtem_gui.os.startfile') as opened:
    app.open_result_folder('saed'); opened.assert_called_once_with(str(ROOT.resolve()))
yy, xx = np.indices((96, 96)); radius = np.hypot(xx-48, yy-48)
image = 100*np.exp(-(radius/3)**2) + 20*np.exp(-0.5*((radius-25)/1.5)**2)
result, _ = analyze_saed(image, max_rings=3, max_spots=3)
editor = open_saed_editor(root, image, result, lambda value: None)
root.update_idletasks()
assert 'Manual correction' in editor.title()
editor.destroy()
print('GUI_SMOKE_OK tabs=' + ','.join(tabs))
root.destroy()
