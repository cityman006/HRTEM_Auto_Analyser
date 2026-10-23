"""Explicit dataset/frame selection. Pixel calibration is never inferred."""
from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import tifffile
from microscopy_io import SUPPORTED_SUFFIXES, _plain, _to_grayscale


def _tiff_layout(shape, axes):
    if "Y" not in axes or "X" not in axes:
        raise ValueError(f"Dataset has no image Y/X axes: {axes} {shape}")
    spatial = [axes.index("Y"), axes.index("X")]
    if "S" in axes and shape[axes.index("S")] in (3, 4):
        spatial.append(axes.index("S"))
    frame_axes = [i for i in range(len(shape)) if i not in spatial]
    return spatial, frame_axes


def list_datasets(path: str | Path) -> list[dict]:
    """Describe image datasets without loading their full pixel arrays.

    dataset uses zero-based physical DM object/TIFF series indices; frame is a
    zero-based flattened index across non-image axes, in C order.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    suffix = source.suffix.lower()
    result = []
    if suffix in (".dm3", ".dm4"):
        from ncempy.io.dm import fileDM
        with fileDM(str(source), on_memory=False) as reader:
            for index in range(reader.numObjects):
                if index == 0 and reader.thumbnail:
                    continue
                shape = tuple(int(v) for v in (reader.zSize2[index], reader.zSize[index],
                                               reader.ySize[index], reader.xSize[index]))
                if min(shape[-2:]) < 2:
                    continue  # A spectrum is not a diffraction/image frame.
                frame_shape = shape[:2]
                result.append({"dataset": index, "shape": list(shape), "axes": "TZYX",
                               "frame_shape": list(frame_shape), "frames": math.prod(frame_shape),
                               "image_shape": list(shape[-2:]), "name": f"DM dataset {index}"})
    elif suffix in (".tif", ".tiff"):
        with tifffile.TiffFile(source) as reader:
            for index, series in enumerate(reader.series):
                try:
                    spatial, frame_axes = _tiff_layout(series.shape, series.axes)
                except ValueError:
                    continue
                frame_shape = [int(series.shape[i]) for i in frame_axes]
                result.append({"dataset": index, "shape": list(series.shape), "axes": series.axes,
                               "frame_shape": frame_shape, "frames": math.prod(frame_shape),
                               "image_shape": [int(series.shape[i]) for i in spatial[:2]],
                               "name": f"TIFF series {index}"})
    elif suffix == ".png":
        from PIL import Image
        with Image.open(source) as image:
            result.append({"dataset": 0, "shape": [image.height, image.width], "axes": "YX",
                           "frame_shape": [], "frames": 1, "image_shape": [image.height, image.width],
                           "name": "PNG image"})
    else:
        raise ValueError(f"Unsupported format: {suffix}; expected {SUPPORTED_SUFFIXES}")
    if not result:
        raise ValueError("No two-dimensional image datasets found.")
    return result


def list_frames(path: str | Path) -> list[dict]:
    source = str(Path(path).resolve())
    return [{"path": source, "dataset": d["dataset"], "frame": f,
             "image_shape": d["image_shape"], "dataset_name": d["name"]}
            for d in list_datasets(path) for f in range(d["frames"])]


def load_frame(path: str | Path, dataset: int = 0, frame: int = 0) -> tuple[np.ndarray, dict]:
    source = Path(path).expanduser().resolve()
    descriptor = next((d for d in list_datasets(source) if d["dataset"] == dataset), None)
    if descriptor is None:
        raise ValueError(f"Dataset {dataset} is not an image dataset in {source.name}.")
    if not 0 <= frame < descriptor["frames"]:
        raise IndexError(f"Frame {frame} outside 0..{descriptor['frames']-1}")
    metadata = {"source_path": str(source), "source_format": source.suffix.upper().lstrip("."),
                "dataset": dataset, "frame": frame, "dataset_description": descriptor,
                "calibration_applied": False}
    if source.suffix.lower() in (".dm3", ".dm4"):
        from ncempy.io.dm import fileDM
        with fileDM(str(source), on_memory=False) as reader:
            # ncempy offsets every multi-object file by one; map the physical
            # object explicitly, including files whose first object is not RGB.
            reader_index = dataset - (1 if reader.numObjects > 1 else 0)
            # Use the memmap API: ncempy 1.16's getSlice has an incorrect 4-D
            # slice offset (z * z2). Flattening leading axes gives the expected
            # C-order frame selection and avoids reading the complete stack.
            mapped = reader.getMemmap(reader_index)
            try:
                pixels = np.asarray(mapped.reshape((-1, *descriptor["image_shape"]))[frame]).copy()
            finally:
                mapped._mmap.close()
            offset = sum(reader.dataShape[:dataset])
            for key, values in (("pixelSize", reader.scale), ("pixelUnit", reader.scaleUnit),
                                ("pixelOrigin", reader.origin)):
                metadata[key] = _plain(values[offset:offset + 2][::-1])
            metadata["reader"] = "ncempy.io.dm.fileDM.getMemmap"
    elif source.suffix.lower() in (".tif", ".tiff"):
        with tifffile.TiffFile(source) as reader:
            series = reader.series[dataset]
            spatial, frame_axes = _tiff_layout(series.shape, series.axes)
            coordinates = np.unravel_index(frame, descriptor["frame_shape"]) if frame_axes else ()
            selectors = [slice(None)] * len(series.shape)
            for axis, value in zip(frame_axes, coordinates):
                selectors[axis] = int(value)
            # tifffile reads compressed series safely. It may load the full
            # series; this is explicit frame support, not a lazy-I/O claim.
            pixels = series.asarray()[tuple(selectors)]
            retained = [i for i in range(len(series.shape)) if i in spatial]
            pixels = np.transpose(pixels, [retained.index(i) for i in spatial])
            metadata["reader"] = "tifffile.TiffFile.series"
    else:
        from PIL import Image
        with Image.open(source) as image:
            pixels = np.asarray(image.convert("RGB") if image.mode in ("P", "RGBA") else image).copy()
        metadata["reader"] = "Pillow.Image"
    pixels = _to_grayscale(pixels)
    metadata["shape"] = list(pixels.shape)
    return pixels, metadata
