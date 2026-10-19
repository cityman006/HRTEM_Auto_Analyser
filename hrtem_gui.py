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

from hrtem_export import load_settings, settings_path

ROOT = Path(__file__).resolve().parent
CORE = ROOT / 'hrtem_spacing.py'
DEFAULT_IMAGE = ROOT / '位错分析.tif'


class HRTEMApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title('HRTEM 晶格间距自动分析 | FFT + ACF')
        root.geometry('1000x720')
        root.minsize(830, 590)
        root.configure(background='#f5f7fb')
        self.running = False
        self.last_output: Path | None = None
        self.export_settings, self.export_warning = load_settings(ROOT)
        self.image_path = tk.StringVar(value=str(DEFAULT_IMAGE) if DEFAULT_IMAGE.is_file() else '')
        self.grid_value = tk.StringVar(value='4x4')
        self.minimum = tk.StringVar(value='4')
        self.maximum = tk.StringVar(value='15')
        self.calibration = tk.StringVar(value='')
        self.open_image_after = tk.BooleanVar(value=True)
        self.pick_roi = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='准备就绪。请选择一张图片，然后点击“开始分析”。')
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

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='高分辨 TEM 晶格条纹自动识别', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='局部 FFT 峰值识别 + 自相关校核；自动保存标注图、数据 CSV、频谱验证图', style='Body.TLabel').pack(anchor='w', pady=(4, 14))

        file_frame = ttk.LabelFrame(frame, text=' 1. 选择图像 ', padding=(10, 9))
        file_frame.pack(fill='x')
        file_frame.columnconfigure(0, weight=1)
        ttk.Entry(file_frame, textvariable=self.image_path).grid(row=0, column=0, sticky='ew', padx=(0, 8))
        ttk.Button(file_frame, text='浏览…', command=self.pick_file).grid(row=0, column=1)
        ttk.Label(file_frame, text='支持 TIFF / TIF / PNG；随包附带“位错分析.tif”，无需再复制。').grid(row=1, column=0, columnspan=2, sticky='w', pady=(7, 0))

        settings = ttk.LabelFrame(frame, text=' 2. 分析参数 ', padding=(10, 10))
        settings.pack(fill='x', pady=(12, 0))
        for col in (1, 3, 5):
            settings.columnconfigure(col, weight=1)
        ttk.Label(settings, text='自动分区:').grid(row=0, column=0, sticky='w', padx=(0, 6), pady=5)
        ttk.Combobox(settings, textvariable=self.grid_value, values=['1x1','2x2','4x4','8x8'], width=12, state='readonly').grid(row=0, column=1, sticky='ew', padx=(0, 18))
        ttk.Label(settings, text='最小周期(px):').grid(row=0, column=2, sticky='w', padx=(0, 6))
        ttk.Entry(settings, textvariable=self.minimum, width=10).grid(row=0, column=3, sticky='ew', padx=(0, 18))
        ttk.Label(settings, text='最大周期(px):').grid(row=0, column=4, sticky='w', padx=(0, 6))
        ttk.Entry(settings, textvariable=self.maximum, width=10).grid(row=0, column=5, sticky='ew')

        ttk.Label(settings, text='像素标定(nm/px):').grid(row=1, column=0, sticky='w', pady=5)
        ttk.Entry(settings, textvariable=self.calibration).grid(row=1, column=1, sticky='ew', padx=(0, 18))
        ttk.Label(settings, text='没有仪器标定时请留空，输出 px，不会虚构 nm。', foreground='#a04900').grid(row=1, column=2, columnspan=4, sticky='w')
        ttk.Checkbutton(settings, text='运行后打开标注图', variable=self.open_image_after).grid(row=2, column=0, columnspan=3, sticky='w', pady=(6, 0))
        ttk.Checkbutton(settings, text='手动选择一个 ROI 区域', variable=self.pick_roi).grid(row=2, column=3, columnspan=3, sticky='w', pady=(6, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=(12, 8))
        self.run_button = ttk.Button(actions, text='▶ 开始分析', style='Action.TButton', command=self.run_analysis)
        self.run_button.pack(side='left')
        ttk.Button(actions, text='打开结果文件夹', command=self.open_output).pack(side='left', padx=(12, 0))
        ttk.Button(actions, text='运行FFT自检', command=self.run_selftest).pack(side='left', padx=(12, 0))
        ttk.Button(actions, text='输出设置', command=self.show_export_settings).pack(side='left', padx=(12, 0))
        ttk.Button(actions, text='按当前设置重新导出', command=self.reexport_current).pack(side='left', padx=(12, 0))
        ttk.Button(actions, text='标定与结果说明', command=self.show_help).pack(side='right')

        ttk.Label(frame, textvariable=self.status, foreground='#145e43', font=('Microsoft YaHei UI', 10, 'bold'), wraplength=900).pack(anchor='w', pady=(3, 8))
        logger = ttk.LabelFrame(frame, text=' 3. 程序运行日志 / 结果 ', padding=(8, 8))
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

    def _append(self, line):
        self.log.configure(state='normal')
        self.log.insert('end', line)
        self.log.see('end')
        self.log.configure(state='disabled')

    def pick_file(self):
        path = filedialog.askopenfilename(
            title='选择 HRTEM 图像',
            filetypes=[('HRTEM image', '*.tif *.tiff *.png'), ('All files', '*.*')]
        )
        if path:
            self.image_path.set(path)

    def _validate(self):
        source = Path(self.image_path.get().strip().strip('"'))
        if not source.is_file():
            raise ValueError('找不到输入图片。请使用“浏览”选择图片。')
        if source.suffix.lower() not in ('.tif','.tiff','.png'):
            raise ValueError('请选择 TIF、TIFF 或 PNG 图像。')
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
        self.last_output = out
        self._append('\n=== START: ' + source.name + ' ===\n')
        self._append('Calibration: ' + (f'{nm} nm/px\n' if nm else 'NOT SUPPLIED: px only\n'))
        self._launch(args, output=out)

    def show_export_settings(self):
        from hrtem_export_ui import open_settings
        def saved(settings):
            self.export_settings = settings
            self._append('Export settings saved: ' + str(settings_path(ROOT)) + '\n')
        open_settings(self.root, ROOT, self.export_settings, saved)

    def reexport_current(self):
        if self.running:
            return
        session = self.last_output
        if session is None or not (session / 'analysis_session.json').is_file():
            selected = filedialog.askdirectory(parent=self.root,
                title='选择包含 analysis_session.json 的新版本分析结果目录', initialdir=str(ROOT / 'outputs'))
            if not selected:
                return
            session = Path(selected)
        if not (session / 'analysis_session.json').is_file():
            messagebox.showerror('无法重新导出',
                '旧版结果缺少已保存的诊断会话。请先对原图进行一次新版本分析；不会暗中重算 FFT。')
            return
        out = ROOT / 'outputs' / (session.name + '_reexport_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        self.last_output = out
        self._append('\n=== Re-export from saved measurements; no FFT or ACF recomputation ===\n')
        self._launch([sys.executable, str(CORE), '--re-export-session', str(session),
                      '-o', str(out), '--export-settings', str(settings_path(ROOT))], output=out)

    def run_selftest(self):
        if self.running:
            return
        self._append('\n=== Synthetic FFT self-test ===\n')
        self._launch([sys.executable, str(CORE), '--self-test'])

    def _launch(self, args, output=None):
        self.running = True
        self.run_button.configure(state='disabled')
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
        if result != 0:
            self.status.set('分析失败，请查看下方日志。可用 03_CHECK_AND_REPAIR.cmd 修复环境。')
            messagebox.showerror('分析失败', '分析进程退出码：' + str(result) + '\n请将窗口中的日志发给我排查。')
            return
        if output is None:
            self.status.set('FFT 合成条纹自检通过。')
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

    def open_output(self):
        folder = self.last_output if self.last_output and self.last_output.is_dir() else ROOT / 'outputs'
        if not folder.is_dir():
            folder = ROOT / 'sample_results'
        if os.name == 'nt':
            os.startfile(str(folder))
        else:
            messagebox.showinfo('结果路径', str(folder))

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
