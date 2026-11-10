"""Portable, non-pickle workbench projects and named parameter presets.

Projects contain editable JSON state and cached numeric arrays.  Source hashes
are captured when an image is opened, never silently refreshed during a save.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import zipfile

import numpy as np

PROJECT_SCHEMA = "hrtem-workbench-project"
PROJECT_VERSION = 1
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARRAY_BYTES = 4 * 1024 ** 3
_ARRAY_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_:\-]{0,255}$")


def hash_source(path: str | Path) -> str:
    """SHA-256 without loading a microscopy source file into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_source(path: str | Path, dataset: int = 0, frame: int = 0) -> dict:
    source = Path(path).expanduser().resolve()
    return {"path": str(source), "sha256": hash_source(source),
            "dataset": int(dataset), "frame": int(frame)}


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON state must use string dictionary keys.")
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: dict) -> bytes:
    try:
        data = json.dumps(_plain(value), ensure_ascii=False, allow_nan=False,
                          indent=2).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Project state must be finite JSON data: {exc}") from exc
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("JSON metadata exceeds the 16 MiB limit; store pixels as arrays.")
    return data


def _read_json(data: bytes) -> dict:
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("JSON metadata exceeds the 16 MiB limit.")
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")
    value = json.loads(data.decode("utf-8-sig"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON metadata must contain an object.")
    return value


def _validate_state(state: dict) -> None:
    if not isinstance(state, dict):
        raise ValueError("Project state must be a dictionary.")
    source = state.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Project source must be an object.")
    if source.get("path") is not None and not isinstance(source["path"], str):
        raise ValueError("Source path must be text or null.")
    digest = source.get("sha256")
    if digest not in (None, "") and (not isinstance(digest, str) or
                                   not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
        raise ValueError("Source sha256 must be a 64-digit hexadecimal digest.")
    for key in ("dataset", "frame"):
        if key in source and (type(source[key]) is not int or source[key] < 0):
            raise ValueError(f"Source {key} must be a nonnegative integer.")


def _array_metadata(arrays: dict) -> dict:
    if not isinstance(arrays, dict):
        raise ValueError("Project arrays must be a dictionary.")
    metadata = {}
    total = 0
    for key, value in arrays.items():
        if not isinstance(key, str) or not _ARRAY_NAME.fullmatch(key):
            raise ValueError(f"Invalid array name: {key!r}")
        arr = np.asarray(value)
        if arr.dtype.hasobject or arr.dtype.kind not in "biufc":
            raise ValueError(f"Array {key} must have a numeric, non-object dtype.")
        if arr.ndim > 6:
            raise ValueError("Project arrays may have at most 6 dimensions.")
        total += arr.nbytes
        metadata[key] = {"shape": list(arr.shape), "dtype": str(arr.dtype),
                         "nbytes": arr.nbytes}
    if total > MAX_ARRAY_BYTES:
        raise ValueError("Cached arrays exceed the 4 GiB project limit.")
    return metadata


def _atomic_write(path: Path, writer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                            dir=path.parent)
    try:
        with os.fdopen(descriptor, "w+b") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_project(path: str | Path, state: dict, arrays: dict) -> None:
    """Atomically save all editable state and numeric caches into one ZIP.

    The caller owns the source hash baseline.  Relative source references are
    interpreted relative to this project file, and no source files are copied.
    """
    path = Path(path).expanduser().resolve()
    clean = _read_json(_json_bytes(state))
    _validate_state(clean)
    metadata = _array_metadata(arrays)
    source = clean.get("source", {})
    if source.get("path"):
        original = Path(source["path"]).expanduser()
        if original.is_absolute():
            try:
                source["path"] = os.path.relpath(original, path.parent)
            except ValueError:  # Windows sources on a different drive.
                source["path"] = str(original)
    now = datetime.now(timezone.utc).isoformat()
    manifest = {"schema": PROJECT_SCHEMA, "version": PROJECT_VERSION,
                "saved_at": now, "state": clean, "arrays": metadata}
    payload = _json_bytes(manifest)

    def write(stream):
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=4, allowZip64=True) as archive:
            archive.writestr("manifest.json", payload)
            # The inner NPZ is streamed to disk, avoiding a second whole image
            # allocation. The outer ZIP handles compression.
            with tempfile.TemporaryFile(dir=path.parent) as cache:
                np.savez(cache, **{key: np.asarray(value) for key, value in arrays.items()})
                cache.seek(0)
                with archive.open("arrays.npz", "w", force_zip64=True) as destination:
                    for block in iter(lambda: cache.read(1024 * 1024), b""):
                        destination.write(block)
    _atomic_write(path, write)


def _manifest(archive: zipfile.ZipFile) -> dict:
    if sorted(archive.namelist()) != ["arrays.npz", "manifest.json"]:
        raise ValueError("Project must contain exactly manifest.json and arrays.npz.")
    if archive.getinfo("manifest.json").file_size > MAX_JSON_BYTES:
        raise ValueError("Project manifest is too large.")
    if archive.getinfo("arrays.npz").file_size > MAX_ARRAY_BYTES + 1024 * 1024:
        raise ValueError("Project array cache is too large.")
    manifest = _read_json(archive.read("manifest.json"))
    if (manifest.get("schema") != PROJECT_SCHEMA or
            type(manifest.get("version")) is not int or manifest["version"] != PROJECT_VERSION):
        raise ValueError("Unsupported project schema or version.")
    _validate_state(manifest.get("state"))
    if not isinstance(manifest.get("arrays"), dict):
        raise ValueError("Project array metadata is missing.")
    return manifest


def read_project_manifest(path: str | Path) -> dict:
    """Read metadata only for history indexing; no array or source decoding."""
    with zipfile.ZipFile(path, "r") as archive:
        return _manifest(archive)


def load_project(path: str | Path) -> tuple[dict, dict, list[str]]:
    """Load a project safely; source changes produce warnings, not lost work."""
    path = Path(path).expanduser().resolve()
    with zipfile.ZipFile(path, "r") as archive:
        manifest = _manifest(archive)
        # Spooled storage spills to disk for large caches.
        with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as cache:
            with archive.open("arrays.npz") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    cache.write(block)
            cache.seek(0)
            with zipfile.ZipFile(cache, "r") as inner:
                if sum(item.file_size for item in inner.infolist()) > MAX_ARRAY_BYTES + 1024 * 1024:
                    raise ValueError("Uncompressed project arrays exceed the size limit.")
            cache.seek(0)
            with np.load(cache, allow_pickle=False) as loaded:
                arrays = {key: loaded[key].copy() for key in loaded.files}
    if _array_metadata(arrays) != manifest["arrays"]:
        raise ValueError("Project array shapes or dtypes do not match the manifest.")
    state = manifest["state"]
    source = state.get("source", {})
    warnings = []
    if source.get("path"):
        original = Path(source["path"]).expanduser()
        if not original.is_absolute():
            original = path.parent / original
        original = original.resolve()
        source["path"] = str(original)
        if not original.is_file():
            warnings.append(f"原始图像不存在：{original}。已恢复保存的缓存；请重新定位原图。")
        elif source.get("sha256"):
            try:
                if hash_source(original).lower() != source["sha256"].lower():
                    warnings.append(f"原始图像哈希不一致：{original}。当前文件与测量时的原图不同；保留原始测量与缓存。")
            except OSError as exc:
                warnings.append(f"无法校验原图哈希：{exc}")
        else:
            warnings.append("此会话未记录原图哈希，无法验证原图是否发生变化。")
    return state, arrays, warnings


def _preset_path(root: str | Path, name: str) -> Path:
    if not isinstance(name, str) or not name or len(name) > 100 or name != name.strip():
        raise ValueError("预设名称必须为 1–100 个字符，且首尾不能有空格。")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(".") or name in (".", ".."):
        raise ValueError("预设名称不能包含路径或 Windows 文件名特殊字符。")
    stem = name.split(".", 1)[0].upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        raise ValueError("预设名称不能使用 Windows 保留设备名。")
    return Path(root).resolve() / "config" / "presets" / (name + ".json")


def save_preset(root: str | Path, name: str, parameters: dict,
                export_settings: dict) -> Path:
    if not isinstance(parameters, dict) or not isinstance(export_settings, dict):
        raise ValueError("Preset parameters and export_settings must be objects.")
    path = _preset_path(root, name)
    payload = _json_bytes({"schema": "hrtem-parameter-preset", "version": 1,
                           "name": name, "parameters": parameters,
                           "export_settings": export_settings})
    _atomic_write(path, lambda stream: stream.write(payload))
    return path


def load_preset(root: str | Path, name: str) -> dict:
    path = _preset_path(root, name)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Preset JSON is too large.")
    value = _read_json(path.read_bytes())
    if (value.get("schema") != "hrtem-parameter-preset" or
            type(value.get("version")) is not int or value["version"] != 1):
        raise ValueError("Unsupported preset schema or version.")
    if not isinstance(value.get("parameters"), dict) or not isinstance(value.get("export_settings"), dict):
        raise ValueError("Preset parameters and export_settings must be objects.")
    return value


def list_presets(root: str | Path) -> list[str]:
    folder = Path(root).resolve() / "config" / "presets"
    return sorted((file.stem for file in folder.glob("*.json") if file.is_file()), key=str.casefold)
