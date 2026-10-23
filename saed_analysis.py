#!/usr/bin/env python3
"""Independent SAED diffraction-ring and diffraction-spot workflow."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, maximum_filter
from scipy.signal import find_peaks

from annotation_export import export_saed_annotation
from hrtem_export import load_settings, load_settings_file
from microscopy_io import load_microscopy_image

SCHEMA = "hrtem-auto-analyser.saed.v1"


def detect_diffraction_center(image: np.ndarray) -> tuple[float, float]:
    """Locate the direct beam using a smoothed peak and local weighted centroid."""
    smooth = gaussian_filter(np.asarray(image, dtype=float), sigma=max(1.5, min(image.shape) / 320))
    h, w = smooth.shape
    margin_y, margin_x = int(h * 0.15), int(w * 0.15)
    central = smooth[margin_y:h-margin_y, margin_x:w-margin_x]
    py, px = np.unravel_index(int(np.argmax(central)), central.shape)
    py += margin_y
    px += margin_x
    radius = max(6, int(min(h, w) * 0.035))
    y0, y1 = max(0, py-radius), min(h, py+radius+1)
    x0, x1 = max(0, px-radius), min(w, px+radius+1)
    local = smooth[y0:y1, x0:x1]
    base = float(np.percentile(local, 30))
    weights = np.clip(local - base, 0, None)
    cap = float(np.percentile(weights, 99.5))
    if cap > 0:
        weights = np.minimum(weights, cap)
    total = float(weights.sum())
    if total <= 0:
        return float(px), float(py)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return float((xx * weights).sum() / total), float((yy * weights).sum() / total)


def radial_profile(image: np.ndarray, center: tuple[float, float]) -> np.ndarray:
    cy, cx = center[1], center[0]
    yy, xx = np.indices(image.shape)
    radii = np.hypot(xx - cx, yy - cy).astype(np.int32)
    sums = np.bincount(radii.ravel(), weights=np.asarray(image, float).ravel())
    counts = np.bincount(radii.ravel())
    return sums / np.maximum(counts, 1)


def _ring_record(radius: float, index: int, calibration: float | None, strength: float) -> dict:
    g = radius * calibration if calibration is not None else None
    return {
        "id": index,
        "radius_px": float(radius),
        "reciprocal_radius_px": float(radius),
        "g_nm_inv": float(g) if g is not None else None,
        "d_nm": float(1.0 / g) if g is not None and g > 0 else None,
        "strength": float(strength),
        "manual": False,
    }


def detect_rings(
    image: np.ndarray,
    center: tuple[float, float],
    *,
    calibration_nm_inv_per_px: float | None = None,
    max_rings: int = 12,
    min_radius_px: float = 8.0,
) -> tuple[list[dict], np.ndarray]:
    profile = radial_profile(image, center)
    max_radius = int(min(image.shape) * 0.47)
    profile = profile[:max_radius]
    smooth = gaussian_filter1d(profile, sigma=1.4)
    background = gaussian_filter1d(smooth, sigma=max(8, min(image.shape) / 35))
    enhanced = smooth - background
    valid = enhanced[int(min_radius_px):]
    noise = float(np.median(np.abs(valid - np.median(valid))) * 1.4826)
    prominence = max(noise * 2.5, float(np.ptp(valid)) * 0.025, 1e-12)
    peaks, props = find_peaks(valid, distance=max(4, int(min(image.shape) / 100)),
                              prominence=prominence)
    peaks = peaks + int(min_radius_px)
    prominences = props.get("prominences", np.zeros_like(peaks, dtype=float))
    ranked = sorted(zip(peaks, prominences), key=lambda item: item[1], reverse=True)[:max_rings]
    ranked.sort(key=lambda item: item[0])
    rings = [_ring_record(float(radius), i + 1, calibration_nm_inv_per_px, float(score))
             for i, (radius, score) in enumerate(ranked)]
    return rings, enhanced


def _spot_record(
    x: float,
    y: float,
    center: tuple[float, float],
    intensity: float,
    index: int,
    calibration: float | None,
    *,
    manual: bool = False,
) -> dict:
    dx, dy = x - center[0], center[1] - y
    distance = math.hypot(dx, dy)
    g = distance * calibration if calibration is not None else None
    return {
        "id": index,
        "x_px": float(x), "y_px": float(y),
        "distance_px": float(distance),
        "angle_deg": float(math.degrees(math.atan2(dy, dx)) % 360),
        "intensity": float(intensity),
        "g_nm_inv": float(g) if g is not None else None,
        "d_nm": float(1 / g) if g is not None and g > 0 else None,
        "manual": bool(manual),
    }


def detect_spots(
    image: np.ndarray,
    center: tuple[float, float],
    *,
    calibration_nm_inv_per_px: float | None = None,
    max_spots: int = 48,
    min_radius_px: float = 8.0,
) -> list[dict]:
    data = np.asarray(image, dtype=float)
    highpass = gaussian_filter(data, 0.8) - gaussian_filter(data, max(3.0, min(data.shape) / 80))
    local = highpass == maximum_filter(highpass, size=7, mode="nearest")
    yy, xx = np.indices(data.shape)
    distance = np.hypot(xx - center[0], yy - center[1])
    allowed = (distance >= min_radius_px) & (distance <= min(data.shape) * 0.48)
    sample = highpass[allowed]
    threshold = max(float(np.percentile(sample, 99.3)),
                    float(np.median(sample) + 4 * np.std(sample)))
    candidates = np.argwhere(local & allowed & (highpass >= threshold))
    ranked = sorted(candidates, key=lambda p: highpass[p[0], p[1]], reverse=True)
    accepted: list[tuple[int, int]] = []
    for y, x in ranked:
        if all((x-ox)**2 + (y-oy)**2 >= 36 for oy, ox in accepted):
            accepted.append((int(y), int(x)))
        if len(accepted) >= max_spots:
            break
    return [_spot_record(x, y, center, data[y, x], i + 1,
                         calibration_nm_inv_per_px)
            for i, (y, x) in enumerate(accepted)]


def add_ring(result: dict, radius_px: float) -> dict:
    if radius_px <= 0:
        raise ValueError("Ring radius must be positive.")
    calibration = result["calibration_nm_inv_per_px"]
    result["rings"].append(_ring_record(radius_px, 0, calibration, 0.0) | {"manual": True})
    result["rings"].sort(key=lambda item: item["radius_px"])
    for i, item in enumerate(result["rings"], 1): item["id"] = i
    return result


def add_spot(result: dict, x_px: float, y_px: float, intensity: float | None = None) -> dict:
    h, w = result["image_shape"]
    if not (0 <= x_px < w and 0 <= y_px < h):
        raise ValueError("Spot is outside the image.")
    if intensity is None:
        intensity = 0.0
    result["spots"].append(_spot_record(
        x_px, y_px, tuple(result["center_px"]), intensity,
        len(result["spots"]) + 1, result["calibration_nm_inv_per_px"], manual=True,
    ))
    return result


def delete_item(result: dict, kind: str, item_id: int) -> dict:
    key = "rings" if kind == "ring" else "spots" if kind == "spot" else None
    if key is None:
        raise ValueError("kind must be ring or spot")
    before = len(result[key])
    result[key] = [item for item in result[key] if int(item["id"]) != int(item_id)]
    if len(result[key]) == before:
        raise ValueError(f"No {kind} with id {item_id}.")
    for i, item in enumerate(result[key], 1): item["id"] = i
    return result


def analyze_saed(
    image: np.ndarray,
    *,
    calibration_nm_inv_per_px: float | None = None,
    max_rings: int = 12,
    max_spots: int = 48,
) -> tuple[dict, np.ndarray]:
    if calibration_nm_inv_per_px is not None and calibration_nm_inv_per_px <= 0:
        raise ValueError("Reciprocal calibration must be positive.")
    center = detect_diffraction_center(image)
    rings, profile = detect_rings(image, center,
        calibration_nm_inv_per_px=calibration_nm_inv_per_px, max_rings=max_rings)
    spots = detect_spots(image, center,
        calibration_nm_inv_per_px=calibration_nm_inv_per_px, max_spots=max_spots)
    result = {
        "schema": SCHEMA,
        "image_shape": [int(v) for v in image.shape],
        "center_px": [float(center[0]), float(center[1])],
        "calibration_nm_inv_per_px": calibration_nm_inv_per_px,
        "calibration_valid": calibration_nm_inv_per_px is not None,
        "unit_note": ("d values use the explicit reciprocal calibration."
                      if calibration_nm_inv_per_px is not None else
                      "No reciprocal calibration: radii are in reciprocal-image pixels; d is omitted."),
        "rings": rings,
        "spots": spots,
    }
    return result, profile


def _write_csv(result: dict, output: Path) -> None:
    ring_cols = ["id", "radius_px", "reciprocal_radius_px", "g_nm_inv", "d_nm", "strength", "manual"]
    spot_cols = ["id", "x_px", "y_px", "distance_px", "angle_deg", "intensity", "g_nm_inv", "d_nm", "manual"]
    for filename, rows, cols in (("saed_rings.csv", result["rings"], ring_cols),
                                 ("saed_spots.csv", result["spots"], spot_cols)):
        with (output / filename).open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=cols)
            writer.writeheader(); writer.writerows(rows)


def save_saed_session(image: np.ndarray, profile: np.ndarray, result: dict, output: Path,
                      settings: dict, source_metadata: dict | None = None) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    result = dict(result)
    if source_metadata is not None:
        result["source_metadata"] = source_metadata
    (output / "saed_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    np.savez_compressed(output / "saed_session.npz", image=image, radial_profile=profile)
    _write_csv(result, output)
    exports = export_saed_annotation(image, tuple(result["center_px"]),
                                     result["rings"], result["spots"], output, settings)
    return exports


def reexport_saed(session: Path, output: Path, settings: dict) -> list[Path]:
    result = json.loads((session / "saed_results.json").read_text(encoding="utf-8-sig"))
    with np.load(session / "saed_session.npz") as saved:
        image = saved["image"].copy()
        profile = saved["radial_profile"].copy()
    return save_saed_session(image, profile, result, output, settings,
                             result.get("source_metadata"))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SAED diffraction ring and spot analysis")
    p.add_argument("-i", "--input", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--reciprocal-calibration", type=float,
                   help="verified reciprocal calibration in nm^-1 per image pixel")
    p.add_argument("--max-rings", type=int, default=12)
    p.add_argument("--max-spots", type=int, default=48)
    p.add_argument("--export-settings", type=Path)
    p.add_argument("--re-export-session", type=Path)
    return p


def main() -> int:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parent
    settings = load_settings_file(args.export_settings) if args.export_settings else load_settings(root)[0]
    if args.re_export_session:
        files = reexport_saed(args.re_export_session, args.output, settings)
        print(f"Re-exported saved SAED detections without rerunning analysis: {len(files)} files")
        return 0
    if args.input is None:
        raise SystemExit("--input is required unless --re-export-session is used")
    image, source_metadata = load_microscopy_image(args.input)
    result, profile = analyze_saed(
        image, calibration_nm_inv_per_px=args.reciprocal_calibration,
        max_rings=args.max_rings, max_spots=args.max_spots)
    result["source_path"] = str(args.input.resolve())
    files = save_saed_session(image, profile, result, args.output, settings, source_metadata)
    print(json.dumps({"center_px": result["center_px"], "rings": len(result["rings"]),
                      "spots": len(result["spots"]), "calibration_valid": result["calibration_valid"],
                      "exports": [p.name for p in files]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
