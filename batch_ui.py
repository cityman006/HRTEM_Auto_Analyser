"""Tk batch queue with explicit frame selection and reusable parameter templates."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from batch_analysis import BatchRunner, validate_parameters
from microscopy_frames import list_datasets, list_frames
from microscopy_io import SUPPORTED_SUFFIXES


def parse_frames(text: str, count: int) -> list[int]:
    if text.strip().lower() in ("all", "*", "全部"):
        return list(range(count))
    result = set()
    for token in text.split(","):
        ends = token.strip().split("-")
        if len(ends) == 1:
            values = [int(ends[0])]
        elif len(ends) == 2:
            first, last = map(int, ends)
            if first > last:
                raise ValueError("帧范围必须从小到大。")
            values = range(first, last + 1)
        else:
            raise ValueError("帧格式例如：0,2-5 或 all")
        for value in values:
            if value < 0 or value >= count:
                raise ValueError(f"帧 {value} 超出 0–{count - 1}")
            result.add(value)
    if not result:
        raise ValueError("至少选择一帧。")
    return sorted(result)


class FrameSelector(tk.Toplevel):
    def __init__(self, parent, path):
        super().__init__(parent)
        self.title(f"选择数据集 / 帧 — {Path(path).name}")
        self.geometry("640x360")
        self.path, self.selected = str(Path(path).resolve()), None
        self.datasets = list_datasets(path)
        ttk.Label(self, text="选择一个或多个数据集（Ctrl/Shift 多选）。编号均从 0 开始。").pack(pady=8)
        self.tree = ttk.Treeview(self, columns=("dataset", "shape", "frames"), show="headings", selectmode="extended")
        for key, label in (("dataset", "数据集"), ("shape", "完整尺寸"), ("frames", "帧数")):
            self.tree.heading(key, text=label)
        self.tree.pack(fill="both", expand=True, padx=10)
        for descriptor in self.datasets:
            self.tree.insert("", "end", iid=str(descriptor["dataset"]),
                             values=(descriptor["name"], str(descriptor["shape"]), descriptor["frames"]))
        self.tree.selection_set(str(self.datasets[0]["dataset"]))
        row = ttk.Frame(self); row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="帧：").pack(side="left")
        self.frames = tk.StringVar(value="all")
        ttk.Entry(row, textvariable=self.frames).pack(side="left", fill="x", expand=True)
        ttk.Label(row, text="all / 0,2-5").pack(side="left", padx=8)
        row = ttk.Frame(self); row.pack(pady=8)
        ttk.Button(row, text="添加所选帧", command=self.accept).pack(side="left", padx=5)
        ttk.Button(row, text="取消", command=self.destroy).pack(side="left", padx=5)
        self.transient(parent)

    def accept(self):
        try:
            chosen = set(self.tree.selection())
            if not chosen:
                raise ValueError("请选择数据集。")
            self.selected = [{"path": self.path, "dataset": d["dataset"], "frame": frame}
                             for d in self.datasets if str(d["dataset"]) in chosen
                             for frame in parse_frames(self.frames.get(), d["frames"])]
            self.destroy()
        except Exception as exc:
            messagebox.showerror("选择无效", str(exc), parent=self)


class BatchWindow(tk.Toplevel):
    def __init__(self, parent, root: Path, params: dict, settings: dict, on_complete=None):
        super().__init__(parent)
        self.root, self.parameters = Path(root), validate_parameters(params)
        self.settings, self.on_complete = copy.deepcopy(settings), on_complete
        self.items, self.runner = [], None
        self.events = queue.Queue()
        self.title("批量分析 — HRTEM / ROI IFFT / SAED")
        self.geometry("1080x650")
        self.protocol("WM_DELETE_WINDOW", self.close)
        top = ttk.Frame(self); top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="流程：").pack(side="left")
        self.mode = tk.StringVar(value="HRTEM")
        self.mode_box = ttk.Combobox(top, textvariable=self.mode, values=("HRTEM", "ROI_IFFT", "SAED"), state="readonly", width=12)
        self.mode_box.pack(side="left", padx=5)
        self.all_frames = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="添加文件时选择全部数据集和帧", variable=self.all_frames).pack(side="left", padx=8)
        self.recursive = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="包含子文件夹", variable=self.recursive).pack(side="left", padx=8)
        buttons = ttk.Frame(self); buttons.pack(fill="x", padx=10)
        for label, action in (("添加文件", self.add_files), ("添加文件夹", self.add_folder),
                              ("移除选中", self.remove), ("参数设置", self.edit_parameters),
                              ("保存参数模板", self.save_template), ("加载参数模板", self.load_template)):
            ttk.Button(buttons, text=label, command=action).pack(side="left", padx=3)
        self.parameter_summary = tk.StringVar()
        ttk.Label(self, textvariable=self.parameter_summary, wraplength=1030).pack(fill="x", padx=12, pady=8)
        self.update_parameter_summary()
        table = ttk.Frame(self); table.pack(fill="both", expand=True, padx=10)
        self.tree = ttk.Treeview(table, columns=("path", "dataset", "frame", "status", "duration", "error"), show="headings", selectmode="extended")
        for key, label, width in (("path", "输入文件", 320), ("dataset", "数据集", 65), ("frame", "帧", 50),
                                  ("status", "状态", 90), ("duration", "用时(s)", 70), ("error", "错误", 300)):
            self.tree.heading(key, text=label); self.tree.column(key, width=width)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y"); self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", self.open_item)
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=8)
        self.status = tk.StringVar(value="加入文件，确认标定和参数后开始。结果表格双击打开输出目录。")
        ttk.Label(self, textvariable=self.status, wraplength=1040).pack(fill="x", padx=10)
        bottom = ttk.Frame(self); bottom.pack(fill="x", padx=10, pady=10)
        ttk.Button(bottom, text="开始新批次", command=self.start).pack(side="left", padx=4)
        ttk.Button(bottom, text="取消当前批次", command=self.cancel).pack(side="left", padx=4)
        ttk.Button(bottom, text="重试失败 / 取消项", command=self.retry).pack(side="left", padx=4)
        ttk.Button(bottom, text="打开汇总目录", command=self.open_output).pack(side="left", padx=4)
        self.after(100, self.poll)

    def idle_required(self):
        if self.runner and self.runner.running:
            messagebox.showinfo("批次正在运行", "请等待完成或取消当前批次。", parent=self)
            return False
        return True

    def add_paths(self, paths):
        if not self.idle_required():
            return
        seen = {(item["path"], item["dataset"], item["frame"]) for item in self.items}
        original_count = len(self.items)
        errors = []
        for path in paths:
            try:
                if self.all_frames.get():
                    selected = list_frames(path)
                else:
                    dialog = FrameSelector(self, path)
                    self.wait_window(dialog)
                    selected = dialog.selected or []
                for item in selected:
                    key = (item["path"], item["dataset"], item["frame"])
                    if key not in seen:
                        self.items.append(item); seen.add(key)
            except Exception as exc:
                errors.append(f"{Path(path).name}: {exc}")
        if len(self.items) != original_count:
            # A retry belongs to an immutable old queue; new items start a new
            # batch instead of acquiring missing ids in the previous runner.
            self.runner = None
        self.refresh()
        if errors:
            messagebox.showwarning("部分文件未加入", "\n".join(errors[:20]), parent=self)

    def add_files(self):
        paths = filedialog.askopenfilenames(parent=self, title="选择图像", filetypes=[("显微图像", "*.dm3 *.dm4 *.tif *.tiff *.png")])
        self.add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            paths = Path(folder).rglob("*") if self.recursive.get() else Path(folder).glob("*")
            self.add_paths(sorted(p for p in paths if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES))

    def remove(self):
        if self.idle_required():
            indices = {int(i) for i in self.tree.selection()}
            self.items = [item for index, item in enumerate(self.items) if index not in indices]
            self.runner = None
            self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.items):
            self.tree.insert("", "end", iid=str(index), values=(item["path"], item["dataset"], item["frame"],
                item.get("status", "待运行"), item.get("duration_seconds", ""), item.get("error", "")))
        self.progress.configure(maximum=max(1, len(self.items)), value=sum(item.get("status") in
                                ("success", "failed", "cancelled") for item in self.items))

    def update_parameter_summary(self):
        p = self.parameters
        self.parameter_summary.set(f"周期范围 {p['min_spacing']}–{p['max_spacing']} px；峰比 {p['min_peak_ratio']}；"
            f"HRTEM 标定 {p['nm_per_pixel'] or '未标定'} nm/px；SAED 标定 {p['reciprocal_calibration'] or '未标定'} nm⁻¹/px；"
            f"mask 半径 {p['mask_radius']} px；导出 {', '.join(self.settings.get('formats', ['png']))}")

    def edit_parameters(self):
        if not self.idle_required():
            return
        dialog = tk.Toplevel(self); dialog.title("批次统一参数")
        labels = {"min_spacing": "最小周期(px)", "max_spacing": "最大周期(px)", "min_peak_ratio": "最小峰比",
                  "nm_per_pixel": "HRTEM 标定(nm/px，未知留空)", "reciprocal_calibration": "SAED 标定(nm⁻¹/px，未知留空)",
                  "max_rings": "最多衍射环", "max_spots": "最多衍射斑点", "mask_radius": "mask 半径(px)",
                  "auto_spots": "FFT 自动选斑数"}
        variables = {}
        for index, (key, label) in enumerate(labels.items()):
            ttk.Label(dialog, text=label).grid(row=index, column=0, sticky="w", padx=10, pady=4)
            variables[key] = tk.StringVar(value="" if self.parameters[key] is None else str(self.parameters[key]))
            ttk.Entry(dialog, textvariable=variables[key]).grid(row=index, column=1, padx=10)
        def save():
            try:
                self.parameters = validate_parameters({**self.parameters, **{k: v.get() for k, v in variables.items()}})
                self.update_parameter_summary(); dialog.destroy()
            except Exception as exc:
                messagebox.showerror("参数无效", str(exc), parent=dialog)
        ttk.Button(dialog, text="应用", command=save).grid(row=len(labels), column=0, columnspan=2, pady=10)

    def save_template(self):
        target = filedialog.asksaveasfilename(parent=self, title="保存批次参数模板", defaultextension=".json", filetypes=[("参数模板", "*.json")])
        if target:
            Path(target).write_text(json.dumps({"schema": "hrtem-parameter-preset-v1", "mode": self.mode.get(),
                "parameters": self.parameters, "export_settings": self.settings}, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_template(self):
        if not self.idle_required():
            return
        source = filedialog.askopenfilename(parent=self, filetypes=[("参数模板", "*.json")])
        if not source:
            return
        try:
            from hrtem_export import validate_settings
            value = json.loads(Path(source).read_text(encoding="utf-8-sig"))
            params = validate_parameters(value.get("parameters", value.get("params", {})))
            settings = validate_settings(value.get("export_settings", value.get("settings", self.settings)))
            mode = value.get("mode", self.mode.get())
            if mode not in ("HRTEM", "ROI_IFFT", "SAED"):
                raise ValueError("无效分析流程")
            self.parameters, self.settings = params, settings
            self.mode.set(mode); self.update_parameter_summary()
        except Exception as exc:
            messagebox.showerror("模板加载失败", str(exc), parent=self)

    def start(self):
        if not self.idle_required() or not self.items:
            return
        output = self.root / "outputs" / ("batch_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        try:
            self.runner = BatchRunner(self.root, self.items, output, self.mode.get(), self.parameters, self.settings,
                                      callback=lambda kind, item: self.events.put((kind, item)))
            self.items = self.runner.items
            self.runner.start()
            self.mode_box.configure(state="disabled")
            self.status.set(f"正在处理 {len(self.items)} 项；输出：{output}")
        except Exception as exc:
            messagebox.showerror("无法开始", str(exc), parent=self)

    def retry(self):
        if self.idle_required() and self.runner:
            self.runner.start(retry_failed=True)
            self.mode_box.configure(state="disabled")

    def cancel(self):
        if self.runner and self.runner.running:
            self.runner.cancel()
            self.status.set("正在取消；已完成结果和部分输出都会保留。")

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "done":
                    self.mode_box.configure(state="readonly")
                    counts = {name: sum(i.get("status") == name for i in self.items) for name in ("success", "failed", "cancelled")}
                    self.status.set(f"批次结束：成功 {counts['success']}，失败 {counts['failed']}，取消 {counts['cancelled']}；{value}")
                    if self.on_complete:
                        self.on_complete(Path(value))
                elif kind == "error":
                    self.status.set(f"批次错误：{value}")
                self.refresh()
        except queue.Empty:
            pass
        self.after(150, self.poll)

    def open_output(self):
        if self.runner and self.runner.output.is_dir():
            os.startfile(str(self.runner.output))

    def open_item(self, _event=None):
        selected = self.tree.selection()
        if selected:
            path = self.items[int(selected[0])].get("output")
            if path and Path(path).is_dir():
                os.startfile(path)

    def close(self):
        if self.runner and self.runner.running:
            if not messagebox.askyesno("结束批次", "关闭窗口将取消未完成项，已生成结果会保留。继续？", parent=self):
                return
            self.runner.cancel()
            self.status.set("等待当前任务停止后关闭…")
            self.after(100, self._close_when_stopped)
        else:
            self.destroy()

    def _close_when_stopped(self):
        if self.runner and self.runner.running:
            self.after(100, self._close_when_stopped)
        else:
            self.destroy()


def open_batch(parent, root: Path, params: dict, settings: dict, on_complete=None):
    return BatchWindow(parent, root, params, settings, on_complete)
