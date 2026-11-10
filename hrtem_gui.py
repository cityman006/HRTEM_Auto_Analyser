#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows GUI wrapper for the scientific HRTEM lattice-fringe FFT analyzer.

Calls the unchanged scientific CLI in a child process to keep the UI responsive.
Only prints nanometers if an explicit physical nm/pixel calibration is provided.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from hrtem_export import load_settings, settings_path

ROOT = Path(__file__).resolve().parent
CORE = ROOT / 'hrtem_spacing.py'
ROI_CORE = ROOT / 'hrtem_fft_tools.py'
SAED_CORE = ROOT / 'saed_analysis.py'
DEFAULT_IMAGE = ROOT / '位错分析.tif'
IMAGE_TYPES = ('.dm3', '.dm4', '.tif', '.tiff', '.png')


class HRTEMApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('HRTEM Auto Analyser | HRTEM + SAED')
        root.geometry('1100x820')
        root.minsize(900, 680)
        root.configure(background='#f5f7fb')
        self.running = False
        self.last_output: Path | None = None
        self.last_hrtem_output: Path | None = None
        self.last_saed_output: Path | None = None
        self.last_roi_output: Path | None = None
        self.current_job = 'hrtem'
        self.export_settings, self.export_warning = load_settings(ROOT)
        self.image_path = tk.StringVar(value=str(DEFAULT_IMAGE) if DEFAULT_IMAGE.is_file() else '')
        self.grid_value = tk.StringVar(value='4x4')
        self.minimum = tk.StringVar(value='4')
        self.maximum = tk.StringVar(value='15')
        self.calibration = tk.StringVar(value='')
        self.open_image_after = tk.BooleanVar(value=True)
        self.pick_roi = tk.BooleanVar(value=False)
        self.roi_specs = tk.StringVar(value='')
        self.pick_fft_spots = tk.BooleanVar(value=False)
        self.saed_path = tk.StringVar(value='')
        self.saed_calibration = tk.StringVar(value='')
        self.saed_max_rings = tk.StringVar(value='12')
        self.saed_max_spots = tk.StringVar(value='48')
        self.status = tk.StringVar(value='准备就绪。请选择一张图片，然后点击“开始分析”。')
        self.hrtem_result_link = tk.StringVar(value='HRTEM 结果文件夹：尚未生成')
        self.saed_result_link = tk.StringVar(value='SAED 结果文件夹：尚未生成')
        self.hrtem_result_target: Path | None = None
        self.saed_result_target: Path | None = None
        self._build()
        if self.export_warning:
            self._append('EXPORT SETTINGS: ' + self.export_warning + '\n')
        if '--auto-demo' in sys.argv and DEFAULT_IMAGE.is_file():
            self._append('\n首次启动：自动运行随包提供的“位错分析.tif”示例。\n')
            root.after(650, self.run_analysis)

    def _build(self):
        style = ttk.Style(self.root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        elif 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 17, 'bold'), foreground='#12315c')
        style.configure('Body.TLabel', font=('Microsoft YaHei UI', 10))
        style.configure('Action.TButton', font=('Microsoft YaHei UI', 11, 'bold'), padding=(10, 7))
        style.configure('Link.TLabel', font=('Microsoft YaHei UI', 9, 'underline'), foreground='#0b63ce')

        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='HRTEM 晶格与 SAED 衍射分析', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='原有 FFT + ACF 测量保留；SAED 与 ROI/IFFT 使用独立流程', style='Body.TLabel').pack(anchor='w', pady=(3, 8))
        self.notebook = ttk.Notebook(frame)
        self.notebook.pack(fill='x')
        hrtem_tab = ttk.Frame(self.notebook, padding=12)
        saed_tab = ttk.Frame(self.notebook, padding=12)
        export_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(hrtem_tab, text='HRTEM')
        self.notebook.add(saed_tab, text='SAED')
        self.notebook.add(export_tab, text='Export Settings')
        self._build_hrtem_tab(hrtem_tab)
        self._build_saed_tab(saed_tab)
        self._build_export_tab(export_tab)

        workspace_actions = ttk.Frame(frame)
        workspace_actions.pack(fill='x', pady=(8, 0))
        for label, command in [('HRTEM 图像工作区', lambda: self.open_workbench('hrtem')),
                               ('SAED 图像工作区', lambda: self.open_workbench('saed')),
                               ('历史结果浏览器', self.browse_history),
                               ('批量分析', self.open_batch_analysis),
                               ('打开项目文件', self.open_project_file)]:
            ttk.Button(workspace_actions, text=label, command=command).pack(side='left', padx=(0, 8))

        self.status_link = ttk.Label(frame, textvariable=self.status, foreground='#145e43',
                  font=('Microsoft YaHei UI', 10, 'bold'), wraplength=1000, cursor='hand2')
        self.status_link.pack(anchor='w', pady=(8, 6))
        self.status_link.bind('<Button-1>', lambda _event: self.open_result_folder('latest'))
        logger = ttk.LabelFrame(frame, text='程序运行日志 / 结果', padding=(8, 8))
        logger.pack(fill='both', expand=True)
        logger.columnconfigure(0, weight=1)
        logger.rowconfigure(0, weight=1)
        self.log = tk.Text(logger, wrap='word', font=('Consolas', 10), bg='#121b2d', fg='#e4edf6', padx=10, pady=10)
        self.log.grid(row=0, column=0, sticky='nsew')
        sc = ttk.Scrollbar(logger, command=self.log.yview)
        sc.grid(row=0, column=1, sticky='ns')
        self.log.configure(yscrollcommand=sc.set, state='disabled')
        self._append('HRTEM analysis ready. Core method: local FFT + one-dimensional ACF.\n')
        self._append('Results are saved in: <program folder>\\outputs\\<image-name>_<timestamp>\\\n')
        self._append('IMPORTANT: Uncalibrated measurements are in pixels, NOT nanometers.\n')

    def _build_hrtem_tab(self, tab):
        tab.columnconfigure(1, weight=1); tab.columnconfigure(3, weight=1); tab.columnconfigure(5, weight=1)
        ttk.Label(tab, text='HRTEM 图像').grid(row=0, column=0, sticky='w')
        ttk.Entry(tab, textvariable=self.image_path).grid(row=0, column=1, columnspan=4, sticky='ew', padx=6)
        ttk.Button(tab, text='浏览…', command=self.pick_file).grid(row=0, column=5, sticky='e')
        ttk.Label(tab, text='自动分区').grid(row=1, column=0, sticky='w', pady=6)
        ttk.Combobox(tab, textvariable=self.grid_value, values=['1x1','2x2','4x4','8x8'], state='readonly', width=10).grid(row=1, column=1, sticky='ew', padx=6)
        ttk.Label(tab, text='最小周期(px)').grid(row=1, column=2, sticky='w')
        ttk.Entry(tab, textvariable=self.minimum, width=10).grid(row=1, column=3, sticky='ew', padx=6)
        ttk.Label(tab, text='最大周期(px)').grid(row=1, column=4, sticky='w')
        ttk.Entry(tab, textvariable=self.maximum, width=10).grid(row=1, column=5, sticky='ew')
        ttk.Label(tab, text='标定 (nm/px)').grid(row=2, column=0, sticky='w')
        ttk.Entry(tab, textvariable=self.calibration).grid(row=2, column=1, sticky='ew', padx=6)
        ttk.Label(tab, text='留空时只输出 px', foreground='#a04900').grid(row=2, column=2, sticky='w')
        ttk.Checkbutton(tab, text='运行后打开标注图', variable=self.open_image_after).grid(row=2, column=3, columnspan=2, sticky='w')
        ttk.Checkbutton(tab, text='旧流程手选单 ROI', variable=self.pick_roi).grid(row=2, column=5, sticky='w')
        ttk.Label(tab, text='IFFT ROI（x,y,w,h；多个用分号分隔）').grid(row=3, column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Entry(tab, textvariable=self.roi_specs).grid(row=3, column=2, columnspan=3, sticky='ew', padx=6, pady=(6, 0))
        ttk.Checkbutton(tab, text='在 FFT 图中手动选斑', variable=self.pick_fft_spots).grid(row=3, column=5, sticky='w', pady=(6, 0))
        actions = ttk.Frame(tab); actions.grid(row=4, column=0, columnspan=6, sticky='ew', pady=(10, 0))
        self.run_button = ttk.Button(actions, text='▶ 原有 HRTEM 分析', style='Action.TButton', command=self.run_analysis)
        self.run_button.pack(side='left')
        self.roi_button = ttk.Button(actions, text='ROI FFT + 选斑 + IFFT', command=self.run_roi_ifft)
        self.roi_button.pack(side='left', padx=8)
        ttk.Button(actions, text='运行 FFT 自检', command=self.run_selftest).pack(side='left')
        ttk.Button(actions, text='打开结果', command=self.open_output).pack(side='right')
        link = ttk.Label(tab, textvariable=self.hrtem_result_link, style='Link.TLabel', cursor='hand2')
        link.grid(row=5, column=0, columnspan=6, sticky='w', pady=(8, 0))
        link.bind('<Button-1>', lambda _event: self.open_result_folder('hrtem'))

    def _build_saed_tab(self, tab):
        for col in (1, 3, 5): tab.columnconfigure(col, weight=1)
        ttk.Label(tab, text='SAED / DM 图像').grid(row=0, column=0, sticky='w')
        ttk.Entry(tab, textvariable=self.saed_path).grid(row=0, column=1, columnspan=4, sticky='ew', padx=6)
        ttk.Button(tab, text='打开 DM3/DM4/TIFF/PNG…', command=self.pick_saed_file).grid(row=0, column=5)
        ttk.Label(tab, text='倒易标定 (nm⁻¹/px)').grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(tab, textvariable=self.saed_calibration).grid(row=1, column=1, sticky='ew', padx=6)
        ttk.Label(tab, text='最大环数').grid(row=1, column=2, sticky='w')
        ttk.Entry(tab, textvariable=self.saed_max_rings, width=8).grid(row=1, column=3, sticky='ew', padx=6)
        ttk.Label(tab, text='最大斑点数').grid(row=1, column=4, sticky='w')
        ttk.Entry(tab, textvariable=self.saed_max_spots, width=8).grid(row=1, column=5, sticky='ew')
        ttk.Label(tab, text='没有可靠倒易标定时，仅输出半径/距离像素值，不生成 d。', foreground='#a04900').grid(row=2, column=0, columnspan=6, sticky='w')
        actions = ttk.Frame(tab); actions.grid(row=3, column=0, columnspan=6, sticky='ew', pady=(10, 0))
        self.saed_button = ttk.Button(actions, text='▶ 自动识别环和斑点', style='Action.TButton', command=self.run_saed)
        self.saed_button.pack(side='left')
        ttk.Button(actions, text='手动增删/修正', command=self.edit_saed).pack(side='left', padx=8)
        ttk.Button(actions, text='按当前样式重新导出', command=lambda: self.reexport_saved('saed')).pack(side='left')
        link = ttk.Label(tab, textvariable=self.saed_result_link, style='Link.TLabel', cursor='hand2')
        link.grid(row=4, column=0, columnspan=6, sticky='w', pady=(8, 0))
        link.bind('<Button-1>', lambda _event: self.open_result_folder('saed'))

    def _build_export_tab(self, tab):
        ttk.Label(tab, text='字体、字号、颜色、线宽、箭头、圆圈、背景、DPI、SVG 文字模式及 PDF/SVG/PNG/TIFF 格式。', wraplength=950).pack(anchor='w')
        ttk.Label(tab, text='SVG/PDF 中显微图像底图保持位图；文字、箭头、环和斑点为独立矢量对象。', foreground='#145e43').pack(anchor='w', pady=(4, 10))
        buttons = ttk.Frame(tab); buttons.pack(fill='x')
        ttk.Button(buttons, text='打开完整输出设置…', command=self.show_export_settings).pack(side='left')
        ttk.Button(buttons, text='重新导出原有 HRTEM', command=lambda: self.reexport_saved('hrtem')).pack(side='left', padx=8)
        ttk.Button(buttons, text='重新导出 ROI/IFFT', command=lambda: self.reexport_saved('roi')).pack(side='left')
        ttk.Button(buttons, text='重新导出 SAED', command=lambda: self.reexport_saved('saed')).pack(side='left', padx=8)

    def _append(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line)
        self.log.see('end')
        self.log.configure(state='disabled')

    def workbench_parameters(self):
        return {'min_spacing': float(self.minimum.get()), 'max_spacing': float(self.maximum.get()),
                'nm_per_pixel': float(self.calibration.get()) if self.calibration.get().strip() else None,
                'reciprocal_calibration': float(self.saed_calibration.get()) if self.saed_calibration.get().strip() else None,
                'max_rings': int(self.saed_max_rings.get()), 'max_spots': int(self.saed_max_spots.get())}

    def workbench_output(self, output, mode):
        self.last_output = output
        self._set_result_link(mode, output)
        self.status.set('工作区结果已保存（点击打开）：' + str(output))

    def open_workbench(self, mode, session=None):
        try:
            from image_workbench import open_workspace
            source = self.saed_path.get() if mode == 'saed' else self.image_path.get()
            return open_workspace(self.root, ROOT, source=source if source and not session else None,
                mode=mode, params=self.workbench_parameters(), settings=self.export_settings,
                session=session, on_output=self.workbench_output)
        except Exception as exc:
            messagebox.showerror('工作区', str(exc), parent=self.root)

    def browse_history(self):
        from history_browser import open_history
        open_history(self.root, ROOT, lambda path: self.open_workbench('hrtem', session=path),
                     on_style=self.show_export_settings)

    def open_project_file(self):
        path = filedialog.askopenfilename(parent=self.root, title='打开工作区项目',
                                         filetypes=[('HRTEM project', '*.hrtemproj')])
        if path: self.open_workbench('hrtem', session=path)

    def open_batch_analysis(self):
        try:
            from batch_ui import open_batch
            open_batch(self.root, ROOT, self.workbench_parameters(), self.export_settings)
        except Exception as exc:
            messagebox.showerror('批量分析', str(exc), parent=self.root)

    def pick_file(self):
        path = filedialog.askopenfilename(
            title='选择 HRTEM 图像',
            filetypes=[('Microscopy image', '*.dm3 *.dm4 *.tif *.tiff *.png'), ('All files', '*.*')]
        )
        if path:
            self.image_path.set(path)

    def pick_saed_file(self):
        path = filedialog.askopenfilename(
            title='选择 SAED 图像',
            filetypes=[('DM / image', '*.dm3 *.dm4 *.tif *.tiff *.png'), ('All files', '*.*')])
        if path:
            self.saed_path.set(path)

    def _validate(self):
        source = Path(self.image_path.get().strip().strip('"'))
        if not source.is_file():
            raise ValueError('找不到输入图片。请使用“浏览”选择图片。')
        if source.suffix.lower() not in IMAGE_TYPES:
            raise ValueError('请选择 DM3、DM4、TIF、TIFF 或 PNG 图像。')
        if not re.fullmatch(r'\d+x\d+', self.grid_value.get()):
            raise ValueError('分区数量应为 4x4 等格式。')
        min_period = float(self.minimum.get())
        max_period = float(self.maximum.get())
        if not 2 <= min_period < max_period < 2048:
            raise ValueError('周期范围设置错误：请确保 2 ≤ 最小周期 < 最大周期。')
        nm = self.calibration.get().strip()
        if nm and not float(nm) > 0:
            raise ValueError('nm/px 必须是大于 0 的数值。')
        return source, min_period, max_period, float(nm) if nm else None

    def run_analysis(self):
        if self.running:
            return
        try:
            source, min_period, max_period, nm = self._validate()
        except (ValueError, OSError) as exc:
            messagebox.showerror('输入参数有误', str(exc))
            return
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', source.stem)
        out = ROOT / 'outputs' / (safe_name + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        args = [sys.executable, str(CORE), '-i', str(source), '-o', str(out),
                '--grid', self.grid_value.get(), '--min-spacing', str(min_period),
                '--max-spacing', str(max_period)]
        if nm is not None:
            args += ['--nm-per-pixel', str(nm)]
        args += ['--export-settings', str(settings_path(ROOT))]
        if self.pick_roi.get():
            args.append('--pick-roi')
        self.last_output = self.last_hrtem_output = out
        self._append('\n=== START: ' + source.name + ' ===\n')
        self._append('Calibration: ' + (f'{nm} nm/px\n' if nm else 'NOT SUPPLIED: px only\n'))
        self._launch(args, output=out)

    def run_roi_ifft(self):
        if self.running:
            return
        try:
            source, min_period, max_period, nm = self._validate()
            raw_rois = [item.strip() for item in self.roi_specs.get().split(';') if item.strip()]
            for item in raw_rois:
                values = [int(v.strip()) for v in item.split(',')]
                if len(values) != 4 or values[2] < 64 or values[3] < 64:
                    raise ValueError('每个 ROI 必须是 x,y,width,height，且宽高至少 64 px。')
        except (ValueError, OSError) as exc:
            messagebox.showerror('输入参数有误', str(exc)); return
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', source.stem)
        out = ROOT / 'outputs' / (safe_name + '_roi_ifft_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        args = [sys.executable, str(ROI_CORE), '-i', str(source), '-o', str(out),
                '--min-spacing', str(min_period), '--max-spacing', str(max_period),
                '--export-settings', str(settings_path(ROOT))]
        for item in raw_rois: args += ['--roi', item]
        if not raw_rois: args.append('--pick-roi')
        if self.pick_fft_spots.get(): args.append('--pick-spots')
        if nm is not None: args += ['--nm-per-pixel', str(nm)]
        self.last_output = self.last_roi_output = out
        self._append('\n=== ROI FFT / spot selection / IFFT: ' + source.name + ' ===\n')
        self._launch(args, output=out, kind='roi')

    def _validate_saed(self):
        source = Path(self.saed_path.get().strip().strip('"'))
        if not source.is_file() or source.suffix.lower() not in IMAGE_TYPES:
            raise ValueError('请选择存在的 DM3、DM4、TIFF 或 PNG 图像。')
        calibration = self.saed_calibration.get().strip()
        calibration = float(calibration) if calibration else None
        if calibration is not None and calibration <= 0:
            raise ValueError('倒易标定必须大于 0。')
        rings, spots = int(self.saed_max_rings.get()), int(self.saed_max_spots.get())
        if not 1 <= rings <= 100 or not 1 <= spots <= 500:
            raise ValueError('环数应为 1-100，斑点数应为 1-500。')
        return source, calibration, rings, spots

    def run_saed(self):
        if self.running:
            return
        try:
            source, calibration, rings, spots = self._validate_saed()
        except (ValueError, OSError) as exc:
            messagebox.showerror('输入参数有误', str(exc)); return
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', source.stem)
        out = ROOT / 'outputs' / (safe_name + '_saed_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        args = [sys.executable, str(SAED_CORE), '-i', str(source), '-o', str(out),
                '--max-rings', str(rings), '--max-spots', str(spots),
                '--export-settings', str(settings_path(ROOT))]
        if calibration is not None: args += ['--reciprocal-calibration', str(calibration)]
        self.last_output = self.last_saed_output = out
        self._append('\n=== SAED ring and spot analysis: ' + source.name + ' ===\n')
        self._launch(args, output=out, kind='saed')

    def edit_saed(self):
        session = self.last_saed_output
        if session is None or not (session / 'saed_results.json').is_file():
            selected = filedialog.askdirectory(parent=self.root, title='选择包含 saed_results.json 的结果目录', initialdir=str(ROOT/'outputs'))
            if not selected: return
            session = Path(selected)
        try:
            result = json.loads((session/'saed_results.json').read_text(encoding='utf-8-sig'))
            with np.load(session/'saed_session.npz') as saved:
                image = saved['image'].copy(); profile = saved['radial_profile'].copy()
        except Exception as exc:
            messagebox.showerror('无法打开 SAED 会话', str(exc)); return
        from saed_analysis import save_saed_session
        from saed_editor import open_saed_editor
        def saved(edited):
            out = ROOT/'outputs'/(session.name+'_manual_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            save_saed_session(image, profile, edited, out, self.export_settings, edited.get('source_metadata'))
            self.last_output = self.last_saed_output = out
            self._set_result_link('saed', out)
            self.status.set('手动修正已保存并重新导出：'+str(out))
            self._append('Manual SAED corrections saved without rerunning detection: '+str(out)+'\n')
        open_saed_editor(self.root, image, result, saved)

    def show_export_settings(self):
        from hrtem_export_ui import open_settings
        def saved(settings):
            self.export_settings = settings
            self._append('Export settings saved: ' + str(settings_path(ROOT)) + '\n')
        open_settings(self.root, ROOT, self.export_settings, saved)

    def reexport_current(self):
        self.reexport_saved('hrtem')

    def reexport_saved(self, kind):
        if self.running:
            return
        spec = {
            'hrtem': (self.last_hrtem_output, 'analysis_session.json', CORE),
            'saed': (self.last_saed_output, 'saed_results.json', SAED_CORE),
            'roi': (self.last_roi_output, 'summary.json', ROI_CORE),
        }[kind]
        session, marker, program = spec
        if session is None or not (session / marker).is_file():
            selected = filedialog.askdirectory(parent=self.root,
                title='选择包含 '+marker+' 的结果目录', initialdir=str(ROOT / 'outputs'))
            if not selected:
                return
            session = Path(selected)
        if not (session / marker).is_file():
            messagebox.showerror('无法重新导出',
                '所选目录缺少 '+marker+'；需要先保存完整分析会话。')
            return
        out = ROOT / 'outputs' / (session.name + '_reexport_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.last_output = out
        if kind == 'hrtem': self.last_hrtem_output = out
        if kind == 'saed': self.last_saed_output = out
        if kind == 'roi': self.last_roi_output = out
        self._append('\n=== Re-export from saved results; no measurement recomputation ===\n')
        self._launch([sys.executable, str(program), '--re-export-session', str(session),
                      '-o', str(out), '--export-settings', str(settings_path(ROOT))], output=out, kind=kind)

    def run_selftest(self):
        if self.running:
            return
        self._append('\n=== Synthetic FFT self-test ===\n')
        self._launch([sys.executable, str(CORE), '--self-test'])

    def _launch(self, args, output=None, kind='hrtem'):
        self.running = True
        self.current_job = kind
        self.run_button.configure(state='disabled')
        self.roi_button.configure(state='disabled')
        self.saed_button.configure(state='disabled')
        self.status.set('正在运行分析…（窗口不会卡死；运行结果显示在下方）')
        threading.Thread(target=self._worker, args=(args, output), daemon=True).start()

    def _worker(self, args, output):
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            with subprocess.Popen(args, cwd=ROOT, env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8'), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                  errors='replace', bufsize=1, creationflags=flags) as proc:
                assert proc.stdout is not None
                for line in proc.stdout:
                    self.root.after(0, self._append, line)
                result = proc.wait()
            self.root.after(0, self._finish, result, output)
        except Exception as exc:
            self.root.after(0, self._append, 'Execution error: ' + str(exc) + '\n')
            self.root.after(0, self._finish, 1, output)

    def _finish(self, result, output):
        self.running = False
        self.run_button.configure(state='normal')
        self.roi_button.configure(state='normal')
        self.saed_button.configure(state='normal')
        if result != 0:
            self.status.set('分析失败，请查看下方日志。可用 03_CHECK_AND_REPAIR.cmd 修复环境。')
            messagebox.showerror('分析失败', '分析进程退出码：' + str(result) + '\n请将窗口中的日志发给我排查。')
            return
        if output is not None:
            self._set_result_link('saed' if self.current_job == 'saed' else 'hrtem', output)
        if output is None:
            self.status.set('FFT 合成条纹自检通过。')
        elif self.current_job == 'saed':
            try:
                details = json.loads((output/'saed_results.json').read_text(encoding='utf-8-sig'))
                self.status.set(f"SAED 完成：{len(details['rings'])} 个环，{len(details['spots'])} 个斑点；结果位于 {output}")
            except Exception:
                self.status.set('SAED 分析完成：'+str(output))
        elif self.current_job == 'roi':
            try:
                details = json.loads((output/'summary.json').read_text(encoding='utf-8-sig'))
                self.status.set(f"ROI/IFFT 完成：{details['count_rois']} 个 ROI；结果位于 {output}")
            except Exception:
                self.status.set('ROI/IFFT 完成：'+str(output))
        else:
            summary_file = output / 'summary.json'
            try:
                summary = json.loads(summary_file.read_text(encoding='utf-8'))
                counts = str(summary.get('count_ok')) + '/' + str(summary.get('count_rois'))
                mid = summary.get('median_spacing_nm_valid_rois') if summary.get('calibration_valid') else summary.get('median_spacing_px_valid_rois')
                unit = 'nm' if summary.get('calibration_valid') else 'px (未标定)'
                if mid is None:
                    self.status.set(f'分析完成：{counts} 区域通过；没有可报告的有效周期；已输出至 {output}')
                else:
                    self.status.set(f'分析完成：{counts} 区域通过；有效区域中位周期 {mid:.4f} {unit}；已输出至 {output}')
            except (OSError, ValueError, TypeError):
                self.status.set('分析完成！结果保存在：' + str(output))
            picture = output / 'lattice_spacing_annotated.png'
            if self.open_image_after.get() and picture.is_file():
                try:
                    if os.name == 'nt':
                        os.startfile(str(picture))
                except OSError:
                    self._append('Could not open annotated image automatically.\n')

    def _set_result_link(self, kind, folder):
        folder = Path(folder).resolve()
        if kind == 'saed':
            self.saed_result_target = folder
            self.saed_result_link.set('打开 SAED 结果文件夹：' + str(folder))
        else:
            self.hrtem_result_target = folder
            self.hrtem_result_link.set('打开 HRTEM 结果文件夹：' + str(folder))

    def open_result_folder(self, kind):
        if kind == 'saed':
            folder = self.saed_result_target
        elif kind == 'hrtem':
            folder = self.hrtem_result_target
        else:
            folder = self.last_output
        if folder is None or not Path(folder).is_dir():
            folder = ROOT / 'outputs'
        if not Path(folder).is_dir():
            folder = ROOT / 'sample_results'
        if os.name == 'nt':
            os.startfile(str(Path(folder).resolve()))
        else:
            messagebox.showinfo('结果路径', str(folder))

    def open_output(self):
        self.open_result_folder('latest')

    def show_help(self):
        messagebox.showinfo('晶格间距测量说明',
            '1. 默认 4x4 分区，对每个区域提取最强 FFT 晶格周期，并用灰度自相关交叉检查。\n\n'
            '2. 有效物理长度标定 nm/px 可以从原始显微镜文件或已知长度的比例尺获得；没有标定就留空，只报告像素周期。\n\n'
            '3. OK 只是 FFT 与 ACF 结果相符，不表示晶面指数 (hkl) 已经确定。\n\n'
            '4. 本工具不能直接测量位错核心的真实局部应变，也不保证未经原始数据核验的晶格畸变准确性。\n\n'
            '5. 每次运行单独建立 outputs 子目录，保留历史结果。')


def main():
    root = tk.Tk()
    HRTEMApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
