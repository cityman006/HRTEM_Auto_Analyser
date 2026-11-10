#!/usr/bin/env python3
"""Image loading shared by HRTEM and SAED workflows.

DM metadata is returned for traceability, but no physical calibration is applied
implicitly. Scientific units are only emitted when the caller supplies a
verified calibration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from matplotlib import image as mpl_image
import tifffile

SUPPORTED_SUFFIXES = (".dm3", ".dm4", ".tif", ".tiff", ".png")


def _plain(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _to_grayscale(data: np.ndarray) -> np.ndarray:
    image = np.asarray(data)
    image = np.squeeze(image)
    if image.ndim == 3 and image.shape[-1] in (3, 4):
        image = (0.2126 * image[..., 0] + 0.7152 * image[..., 1]
                 + 0.0722 * image[..., 2])
    elif image.ndim == 3 and image.shape[0] in (3, 4):
        image = (0.2126 * image[0] + 0.7152 * image[1]
                 + 0.0722 * image[2])
    if image.ndim != 2:
        raise ValueError(
            f"Expected one two-dimensional image; received shape {image.shape}. "
            "Select/export a single frame before analysis."
        )
    image = image.astype(np.float64, copy=False)
    if not np.isfinite(image).all():
        raise ValueError("Image contains invalid pixel values (NaN/Inf).")
    if float(np.max(image)) == float(np.min(image)):
        raise ValueError("Image has no intensity contrast.")
    return image


def load_microscopy_image(path: str | Path) -> tuple[np.ndarray, dict[str, Any]]:
    """Load DM3/DM4/TIFF/PNG and return grayscale pixels plus safe metadata."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported image type {suffix}; use DM3, DM4, TIFF, or PNG.")

    metadata: dict[str, Any] = {
        "source_path": str(source),
        "source_format": suffix.lstrip(".").upper(),
        "calibration_applied": False,
    }
    if suffix in (".dm3", ".dm4"):
        try:
            from ncempy.io import dm
        except ImportError as exc:
            raise RuntimeError(
                "DM3/DM4 support requires ncempy in this project's .venv."
            ) from exc
        dataset = dm.dmReader(str(source), dSetNum=0, verbose=False, on_memory=True)
        if "data" not in dataset:
            raise ValueError("The DM file does not contain an image dataset.")
        data = dataset["data"]
        for key in ("pixelSize", "pixelUnit", "coords", "filename"):
            if key in dataset:
                metadata[key] = _plain(dataset[key])
        metadata["reader"] = "ncempy.io.dm.dmReader"
    elif suffix in (".tif", ".tiff"):
        data = tifffile.imread(str(source))
        metadata["reader"] = "tifffile.imread"
    else:
        data = mpl_image.imread(str(source))
        metadata["reader"] = "matplotlib.image.imread"

    image = _to_grayscale(data)
    metadata["shape"] = [int(v) for v in image.shape]
    metadata["dtype_after_loading"] = str(image.dtype)
    return image, metadata


def load_grayscale(path: str | Path) -> np.ndarray:
    return load_microscopy_image(path)[0]
