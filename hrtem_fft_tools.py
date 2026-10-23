#!/usr/bin/env python3
"""ROI FFT spot selection, reproducible masks, and inverse FFT tools.

The established HRTEM FFT + ACF measurement remains in hrtem_spacing.py and
is called directly for each ROI. This module adds selection and IFFT output.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter

from annotation_export import export_hrtem_roi_bundle
from hrtem_export import load_settings, load_settings_file
from hrtem_spacing import analyze_roi
from microscopy_io import load_microscopy_image

SCHEMA = "hrtem-auto-analyser.roi-ifft.v1"


def _spectrum(tile: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = tile.shape
    centered = tile - gaussian_filter(tile, sigma=min(16.0, min(h, w) / 12))
    window = np.hanning(h)[:, None] * np.hanning(w)[None, :]
    spectrum = np.fft.fftshift(np.fft.fft2(centered * window))
    return spectrum, np.log1p(np.abs(spectrum))


def detect_fft_spots(
    fft_log: np.ndarray,
    *,
    min_spacing: float,
    max_spacing: float,
    count: int = 2,
    mask_radius_px: float = 6.0,
) -> list[dict]:
    h, w = fft_log.shape
    yy, xx = np.indices(fft_log.shape)
    fy = (yy - h // 2) / h
    fx = (xx - w // 2) / w
    freq = np.hypot(fx, fy)
    half = (fy > 0) | ((fy == 0) & (fx > 0))
    allowed = (freq >= 1 / max_spacing) & (freq <= 1 / min_spacing) & half
    maxima = (fft_log == maximum_filter(fft_log, size=7, mode="nearest")) & allowed
    candidates = np.argwhere(maxima)
    ranked = sorted(candidates, key=lambda p: fft_log[p[0], p[1]], reverse=True)
    selected: list[dict] = []
    for y, x in ranked:
        if any((x-s["x_px"])**2 + (y-s["y_px"])**2 < 49 for s in selected):
            continue
        kx, ky = (x - w // 2) / w, (y - h // 2) / h
        selected.append({
            "x_px": float(x), "y_px": float(y),
            "kx_cycles_per_px": float(kx), "ky_cycles_per_px": float(ky),
            "frequency_cycles_per_px": float(math.hypot(kx, ky)),
            "period_px": float(1 / math.hypot(kx, ky)),
            "angle_deg": float(math.degrees(math.atan2(ky, kx)) % 180),
            "intensity_log": float(fft_log[y, x]),
            "radius_px": float(mask_radius_px), "manual": False,
        })
        if len(selected) >= count:
            break
    if not selected:
        raise ValueError("No FFT spots were found in the requested period range.")
    return selected


def spot_from_xy(shape: tuple[int, int], x: float, y: float, radius: float,
                 fft_log: np.ndarray | None = None) -> dict:
    h, w = shape
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError(f"FFT spot ({x}, {y}) is outside {w}x{h}.")
    if radius <= 0:
        raise ValueError("Mask radius must be positive.")
    kx, ky = (x - w // 2) / w, (y - h // 2) / h
    freq = math.hypot(kx, ky)
    if freq <= 0:
        raise ValueError("The DC center cannot be used as a lattice spot.")
    return {
        "x_px": float(x), "y_px": float(y),
        "kx_cycles_per_px": float(kx), "ky_cycles_per_px": float(ky),
        "frequency_cycles_per_px": float(freq), "period_px": float(1 / freq),
        "angle_deg": float(math.degrees(math.atan2(ky, kx)) % 180),
        "intensity_log": float(fft_log[int(round(y)), int(round(x))]) if fft_log is not None else None,
        "radius_px": float(radius), "manual": True,
    }


def build_conjugate_mask(shape: tuple[int, int], spots: list[dict]) -> np.ndarray:
    """Create circular masks at selected spots and their Friedel counterparts."""
    h, w = shape
    yy, xx = np.indices(shape)
    mask = np.zeros(shape, dtype=bool)
    cx, cy = w // 2, h // 2
    for spot in spots:
        x, y, radius = float(spot["x_px"]), float(spot["y_px"]), float(spot["radius_px"])
        for px, py in ((x, y), (2 * cx - x, 2 * cy - y)):
            mask |= (xx - px) ** 2 + (yy - py) ** 2 <= radius ** 2
    return mask


def inverse_fft(spectrum: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if spectrum.shape != mask.shape:
        raise ValueError("Spectrum and mask shapes differ.")
    restored = np.fft.ifft2(np.fft.ifftshift(spectrum * mask))
    return np.real(restored)


def analyze_roi_ifft(
    image: np.ndarray,
    roi: tuple[int, int, int, int],
    *,
    min_spacing: float = 4.0,
    max_spacing: float = 15.0,
    min_peak_ratio: float = 8.0,
    nm_per_pixel: float | None = None,
    selected_spots: list[dict] | None = None,
    auto_spot_count: int = 2,
    mask_radius_px: float = 6.0,
) -> tuple[dict[str, np.ndarray], dict]:
    row, _diagnostic = analyze_roi(image, roi, min_spacing, max_spacing, min_peak_ratio)
    if row.get("d_fft_px") is None:
        raise ValueError(f"ROI measurement failed: {row['status']}")
    x, y, w, h = roi
    tile = np.asarray(image[y:y+h, x:x+w], dtype=float)
    spectrum, fft_log = _spectrum(tile)
    spots = selected_spots or detect_fft_spots(
        fft_log, min_spacing=min_spacing, max_spacing=max_spacing,
        count=auto_spot_count, mask_radius_px=mask_radius_px)
    mask = build_conjugate_mask(tile.shape, spots)
    restored = inverse_fft(spectrum, mask)
    measurement = dict(row)
    measurement["roi"] = list(roi)
    measurement["d_fft_nm"] = (float(row["d_fft_px"] * nm_per_pixel)
                               if nm_per_pixel is not None else None)
    measurement["d_acf_nm"] = (float(row["d_acf_px"] * nm_per_pixel)
                               if nm_per_pixel is not None and row.get("d_acf_px") is not None else None)
    arrays = {
        "roi": tile, "fft_log": fft_log,
        "mask": mask.astype(np.uint8), "ifft": restored,
    }
    metadata = {
        "schema": SCHEMA, "measurement": measurement,
        "selected_spots": spots,
        "mask_includes_conjugate_spots": True,
        "calibration_nm_per_pixel": nm_per_pixel,
        "calibration_valid": nm_per_pixel is not None,
        "unit_note": ("Physical spacing uses the explicit nm/pixel calibration."
                      if nm_per_pixel is not None else
                      "No reliable nm/pixel calibration: spacing is reported in pixels only."),
        "analysis_parameters": {
            "min_spacing_px": min_spacing, "max_spacing_px": max_spacing,
            "min_peak_ratio": min_peak_ratio, "auto_spot_count": auto_spot_count,
            "default_mask_radius_px": mask_radius_px,
        },
    }
    return arrays, metadata


def parse_roi(value: str) -> tuple[int, int, int, int]:
    values = tuple(int(v.strip()) for v in value.split(","))
    if len(values) != 4:
        raise ValueError("ROI must be x,y,width,height.")
    return values


def interactive_rois(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.imshow(image, cmap="gray", origin="upper")
    ax.set_title("Click two opposite corners per ROI; press Enter when finished")
    points = plt.ginput(n=-1, timeout=0)
    plt.close(fig)
    if len(points) < 2 or len(points) % 2:
        raise ValueError("ROI selection needs pairs of corner clicks.")
    rois = []
    for first, second in zip(points[::2], points[1::2]):
        x0, x1 = sorted((round(first[0]), round(second[0])))
        y0, y1 = sorted((round(first[1]), round(second[1])))
        rois.append((x0, y0, x1-x0, y1-y0))
    return rois


def interactive_spots(fft_log: np.ndarray, radius: float) -> list[dict]:
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(fft_log, cmap="magma", origin="upper")
    ax.set_title("Click one or more FFT spots; press Enter when finished")
    points = plt.ginput(n=-1, timeout=0)
    plt.close(fig)
    if not points:
        raise ValueError("Select at least one FFT spot.")
    return [spot_from_xy(fft_log.shape, x, y, radius, fft_log) for x, y in points]


def _write_measurement_csv(metadata: dict, target: Path) -> None:
    row = metadata["measurement"]
    fields = ["x", "y", "width", "height", "d_fft_px", "d_acf_px", "d_fft_nm",
              "d_acf_nm", "normal_angle_deg", "peak_to_median", "relative_difference", "status"]
    values = dict(row)
    x, y, w, h = values.pop("roi")
    values.update(x=x, y=y, width=w, height=h)
    with target.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerow(values)


def save_roi_session(arrays: dict[str, np.ndarray], metadata: dict, output: Path,
                     settings: dict) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output / "hrtem_roi_session.npz", **arrays)
    (output / "hrtem_roi_result.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_measurement_csv(metadata, output / "hrtem_roi_measurement.csv")
    with (output / "fft_selected_spots.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        fields = ["id", "x_px", "y_px", "kx_cycles_per_px", "ky_cycles_per_px",
                  "frequency_cycles_per_px", "period_px", "angle_deg", "intensity_log",
                  "radius_px", "manual"]
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for i, spot in enumerate(metadata["selected_spots"], 1):
            writer.writerow({"id": i, **spot})
    return export_hrtem_roi_bundle(arrays, metadata, output, settings)


def reexport_roi_session(session: Path, output: Path, settings: dict) -> list[Path]:
    metadata = json.loads((session / "hrtem_roi_result.json").read_text(encoding="utf-8-sig"))
    with np.load(session / "hrtem_roi_session.npz") as saved:
        arrays = {key: saved[key].copy() for key in ("roi", "fft_log", "mask", "ifft")}
    return save_roi_session(arrays, metadata, output, settings)


def reexport_roi_run(session: Path, output: Path, settings: dict) -> list[Path]:
    """Re-export either one ROI folder or every ROI in a saved run."""
    if (session / "hrtem_roi_result.json").is_file():
        return reexport_roi_session(session, output, settings)
    roi_folders = sorted(path for path in session.glob("roi_*")
                         if (path / "hrtem_roi_result.json").is_file())
    if not roi_folders:
        raise FileNotFoundError("No saved ROI/IFFT session was found.")
    all_files: list[Path] = []
    summaries = []
    for index, folder in enumerate(roi_folders, 1):
        destination = output / f"roi_{index:02d}"
        all_files.extend(reexport_roi_session(folder, destination, settings))
        metadata = json.loads((destination / "hrtem_roi_result.json").read_text(encoding="utf-8-sig"))
        summaries.append({"roi_id": index, "roi": metadata["measurement"]["roi"],
                          "status": metadata["measurement"]["status"], "output": str(destination)})
    summary = {"workflow": "HRTEM_ROI_IFFT_REEXPORT", "count_rois": len(summaries),
               "recomputed_measurements": False, "rois": summaries}
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return all_files


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HRTEM ROI FFT spot selection and IFFT")
    parser.add_argument("-i", "--input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    parser.add_argument("--roi", action="append", default=[])
    parser.add_argument("--pick-roi", action="store_true")
    parser.add_argument("--pick-spots", action="store_true")
    parser.add_argument("--spot", action="append", default=[], help="x,y,radius in ROI FFT pixels")
    parser.add_argument("--auto-spots", type=int, default=2)
    parser.add_argument("--mask-radius", type=float, default=6.0)
    parser.add_argument("--min-spacing", type=float, default=4.0)
    parser.add_argument("--max-spacing", type=float, default=15.0)
    parser.add_argument("--min-peak-ratio", type=float, default=8.0)
    parser.add_argument("--nm-per-pixel", type=float)
    parser.add_argument("--export-settings", type=Path)
    parser.add_argument("--re-export-session", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parent
    settings = load_settings_file(args.export_settings) if args.export_settings else load_settings(root)[0]
    if args.re_export_session:
        files = reexport_roi_run(args.re_export_session, args.output, settings)
        print(f"Re-exported saved ROI/IFFT session without measurement recomputation: {len(files)} files")
        return 0
    if args.input is None:
        raise SystemExit("--input is required unless --re-export-session is used")
    image, source_metadata = load_microscopy_image(args.input)
    rois = [parse_roi(value) for value in args.roi]
    if args.pick_roi:
        rois.extend(interactive_rois(image))
    if not rois:
        rois = [(0, 0, image.shape[1], image.shape[0])]
    args.output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for index, roi in enumerate(rois, 1):
        x, y, w, h = roi
        tile = image[y:y+h, x:x+w]
        _spec, fft_log = _spectrum(tile)
        selected = None
        if args.spot:
            selected = []
            for value in args.spot:
                sx, sy, radius = [float(v.strip()) for v in value.split(",")]
                selected.append(spot_from_xy(fft_log.shape, sx, sy, radius, fft_log))
        elif args.pick_spots:
            selected = interactive_spots(fft_log, args.mask_radius)
        arrays, metadata = analyze_roi_ifft(
            image, roi, min_spacing=args.min_spacing, max_spacing=args.max_spacing,
            min_peak_ratio=args.min_peak_ratio, nm_per_pixel=args.nm_per_pixel,
            selected_spots=selected, auto_spot_count=args.auto_spots,
            mask_radius_px=args.mask_radius)
        metadata["source_path"] = str(args.input.resolve())
        metadata["source_metadata"] = source_metadata
        folder = args.output / f"roi_{index:02d}"
        files = save_roi_session(arrays, metadata, folder, settings)
        summaries.append({"roi_id": index, "roi": list(roi), "status": metadata["measurement"]["status"],
                          "d_fft_px": metadata["measurement"]["d_fft_px"],
                          "selected_spots": len(metadata["selected_spots"]),
                          "output": str(folder), "exports": len(files)})
    summary = {"workflow": "HRTEM_ROI_IFFT", "count_rois": len(summaries),
               "calibration_valid": args.nm_per_pixel is not None, "rois": summaries}
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
