"""Result history browser for workbench projects and legacy HRTEM sessions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import warnings
import zipfile

from workbench_storage import MAX_JSON_BYTES, read_project_manifest

LEGACY_NAMES = {"saed_results.json": "SAED", "hrtem_roi_result.json": "ROI / IFFT",
                "analysis_session.json": "HRTEM"}


@dataclass
class HistoryEntry:
    path: Path
    kind: str
    input: str = ""
    calibration: str = "未标定"
    parameters: dict = field(default_factory=dict)
    timestamp: str = ""
    elapsed_seconds: float | None = None
    output: Path | None = None
    thumbnail: Path | None = None
    error: str = ""


def _thumbnail(folder: Path) -> Path | None:
    # Only exported previews, never open the original microscopy source here.
    for name in ("workbench_preview.png", "workbench_annotation.png", "saed_annotated.png", "annotated.png",
                 "lattice_spacing_annotated.png", "roi_annotated.png",
                 "ifft.png", "roi_original.png", "fft_spots.png"):
        path = folder / name
        if path.is_file():
            return path
    for path in sorted(folder.glob("*.png")):
        if path.stat().st_size <= 20 * 1024 * 1024:
            return path
    return None


def _entry(path: Path) -> HistoryEntry:
    kind = "项目" if path.suffix.lower() == ".hrtemproj" else LEGACY_NAMES[path.name]
    entry = HistoryEntry(path=path, kind=kind, output=path.parent,
                         timestamp=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
    try:
        if kind == "项目":
            manifest = read_project_manifest(path)
            state = manifest["state"]
            entry.kind = "项目 / " + str(state.get("mode", "workbench"))
            source = state.get("source", {})
            source_path = source.get("path")
            if source_path:
                resolved = Path(source_path)
                if not resolved.is_absolute():
                    resolved = path.parent / resolved
                entry.input = str(resolved.resolve())
            entry.parameters = state.get("params", state.get("parameters", {}))
            if not isinstance(entry.parameters, dict):
                raise ValueError("项目参数必须为 JSON 对象")
            entry.timestamp = state.get("created_at") or manifest.get("saved_at", entry.timestamp)
            output = state.get("output_dir") or state.get("output_directory")
            if output:
                directory = Path(output)
                entry.output = directory if directory.is_absolute() else (path.parent / directory).resolve()
            calibration = state.get("calibration", {})
            if calibration:
                entry.calibration = json.dumps(calibration, ensure_ascii=False)
            else:
                key = "calibration_nm_inv_per_px" if str(state.get("mode", "")).lower() == "saed" else "nm_per_pixel"
                value = entry.parameters.get(key)
                if value is not None:
                    unit = "nm⁻¹/px" if key == "calibration_nm_inv_per_px" else "nm/px"
                    entry.calibration = f"{value} {unit}"
        else:
            if path.stat().st_size > MAX_JSON_BYTES:
                raise ValueError("历史 JSON 超过 16 MiB")
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(state, dict):
                raise ValueError("历史结果必须为 JSON 对象")
            source = state.get("source_metadata", {})
            if not isinstance(source, dict):
                raise ValueError("历史结果的 source_metadata 必须为 JSON 对象")
            entry.input = str(state.get("source_path") or state.get("source") or
                              source.get("source_path") or "")
            entry.parameters = state.get("analysis_parameters", {})
            if not isinstance(entry.parameters, dict):
                raise ValueError("历史参数必须为 JSON 对象")
            if "diagnostic_roi" in state:
                entry.parameters["diagnostic_roi"] = state["diagnostic_roi"]
            for key, unit in (("calibration_nm_inv_per_px", "nm⁻¹/px"),
                              ("calibration_nm_per_pixel", "nm/px"), ("nm_per_pixel", "nm/px")):
                if state.get(key) is not None:
                    entry.calibration = f"{state[key]} {unit}"
                    break
        duration = state.get("elapsed_seconds")
        if isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0:
            entry.elapsed_seconds = float(duration)
        entry.thumbnail = _thumbnail(entry.output or path.parent)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        entry.error = str(exc)
    return entry


def scan_history(root: str | Path) -> list[HistoryEntry]:
    """Index metadata only. Do not decode sources, NPZ arrays, or thumbnails."""
    root = Path(root).resolve()
    folders = [root / "outputs", root / "projects"]
    if root.name.lower() in ("outputs", "projects"):
        folders = [root]
    files = set(root.glob("*.hrtemproj"))
    for folder in folders:
        if not folder.is_dir():
            continue
        for current, subdirs, names in os.walk(folder):
            subdirs[:] = [name for name in subdirs if name not in
                          {".venv", "venv", "__pycache__", ".git", "backups"}]
            for name in names:
                if name in LEGACY_NAMES or name.lower().endswith(".hrtemproj"):
                    files.add(Path(current) / name)
    entries = []
    for path in files:
        try:
            entries.append(_entry(path))
        except OSError:
            continue  # A result may have been moved during the background scan.
    return sorted(entries, key=lambda entry: entry.timestamp, reverse=True)


class HistoryBrowser(tk.Toplevel):
    def __init__(self, parent, root, on_open, on_style=None):
        super().__init__(parent)
        self.title("结果浏览器 / Result History")
        self.geometry("1120x660")
        self.root_path = Path(root)
        self.on_open = on_open
        self.on_style = on_style
        self.entries = []
        self._photo = None
        self._generation = 0
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="刷新", command=self.refresh).pack(side="left")
        ttk.Button(toolbar, text="打开会话 / 继续修正", command=self.open_selected).pack(side="left", padx=6)
        ttk.Button(toolbar, text="打开结果文件夹", command=self.open_folder).pack(side="left")
        if on_style:
            ttk.Button(toolbar, text="修改导出样式", command=on_style).pack(side="left", padx=6)
        self.status = ttk.Label(toolbar, text="")
        self.status.pack(side="right")
        pane = ttk.Panedwindow(self, orient="vertical")
        pane.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        table = ttk.Frame(pane)
        self.tree = ttk.Treeview(table, columns=("kind", "input", "calibration", "time", "duration"), show="headings")
        for column, title, width in (("kind", "类型", 110), ("input", "输入图像", 440),
                                      ("calibration", "标定", 150), ("time", "运行 / 保存时间", 210),
                                      ("duration", "计算耗时", 90)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.show_selected)
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        pane.add(table, weight=3)
        details = ttk.Frame(pane)
        self.preview = ttk.Label(details, text="选择一项查看缩略图", anchor="center", width=38)
        self.preview.pack(side="left", fill="both", padx=8)
        self.details = tk.Text(details, wrap="word", height=12, state="disabled")
        self.details.pack(side="right", fill="both", expand=True)
        pane.add(details, weight=2)
        self.refresh()

    def refresh(self):
        self._generation += 1
        generation = self._generation
        self.status.configure(text="正在读取结果索引…")
        result = []
        def scan():
            try:
                result.append((scan_history(self.root_path), None))
            except Exception as exc:
                result.append(([], str(exc)))
        worker = threading.Thread(target=scan, daemon=True)
        worker.start()
        def poll():
            if not self.winfo_exists() or generation != self._generation:
                return
            if worker.is_alive():
                self.after(80, poll)
                return
            self.entries, error = result[0]
            self.tree.delete(*self.tree.get_children())
            for index, entry in enumerate(self.entries):
                self.tree.insert("", "end", iid=str(index), values=(entry.kind, entry.input,
                                 entry.calibration, entry.timestamp,
                                 f"{entry.elapsed_seconds:.2f} s" if entry.elapsed_seconds is not None else "未记录"))
            self.status.configure(text=f"{len(self.entries)} 条记录" if not error else error)
        self.after(80, poll)

    def selected(self):
        selected = self.tree.selection()
        return self.entries[int(selected[0])] if selected else None

    def show_selected(self, _event=None):
        entry = self.selected()
        if not entry:
            return
        duration = f"{entry.elapsed_seconds:.3f} s" if entry.elapsed_seconds is not None else "未记录"
        content = (f"输入：{entry.input}\n标定：{entry.calibration}\n时间：{entry.timestamp}\n"
                   f"计算耗时：{duration}\n")
        content += (
                   f"输出目录：{entry.output}\n会话：{entry.path}\n参数：\n"
                   + json.dumps(entry.parameters, ensure_ascii=False, indent=2))
        if entry.error:
            content += "\n读取错误：" + entry.error
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", content)
        self.details.configure(state="disabled")
        self.preview.configure(image="", text="无预览图")
        self._photo = None
        if entry.thumbnail:
            try:
                from PIL import Image, ImageTk
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(entry.thumbnail) as picture:
                        picture.thumbnail((290, 220))
                        self._photo = ImageTk.PhotoImage(picture.convert("RGB"), master=self)
                self.preview.configure(image=self._photo, text="")
            except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                self.preview.configure(text="此预览图过大或无法读取")

    def open_selected(self):
        entry = self.selected()
        if not entry:
            return
        if entry.error:
            messagebox.showerror("无法打开", entry.error, parent=self)
            return
        try:
            self.on_open(entry.path)
        except Exception as exc:
            messagebox.showerror("打开会话失败", str(exc), parent=self)

    def open_folder(self):
        entry = self.selected()
        if entry and entry.output:
            try:
                os.startfile(str(entry.output))
            except OSError as exc:
                messagebox.showerror("打开文件夹失败", str(exc), parent=self)


def open_history(parent, root, on_open, on_style=None):
    return HistoryBrowser(parent, root, on_open, on_style)
