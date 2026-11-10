#!/usr/bin/env python3
"""Mixed raster/vector exports for SAED and ROI/IFFT results.

Microscopy pixels remain raster data. Matplotlib text, circles, arrows and
lines remain vector objects in SVG/PDF output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import matplotlib as mpl
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle
import numpy as np

from hrtem_export import resolve_font, validate_settings


def _figure(settings: dict, shape: tuple[int, int]) -> Figure:
    width = settings["width_mm"] / 25.4
    height = (settings["height_mm"] / 25.4 if settings["height_mm"]
              else width * shape[0] / max(shape[1], 1))
    if width * height * settings["dpi"] ** 2 > 100_000_000:
        raise ValueError("Output would exceed 100 million pixels; reduce size or DPI.")
    fig = Figure(
        figsize=(width, height), dpi=settings["dpi"],
        facecolor="none" if settings["background"] == "transparent" else "white",
    )
    FigureCanvasAgg(fig)
    return fig


def _axis(fig: Figure, image: np.ndarray, settings: dict, *, cmap: str = "gray"):
    ax = fig.add_axes([0.08, 0.08, 0.86, 0.84] if settings["show_axes"] else [0, 0, 1, 1])
    finite = np.asarray(image)[np.isfinite(image)]
    low, high = np.percentile(finite, [0.2, 99.8])
    if high <= low:
        low, high = float(np.min(finite)), float(np.max(finite) + 1e-12)
    ax.imshow(image, cmap=cmap, origin="upper", interpolation="nearest", vmin=low, vmax=high)
    if not settings["show_axes"]:
        ax.set_axis_off()
    else:
        axis_font = resolve_font(settings, settings["axis_size"])
        tick_font = resolve_font(settings, settings["tick_size"])
        ax.set_xlabel("x (pixels)", fontproperties=axis_font)
        ax.set_ylabel("y (pixels)", fontproperties=axis_font)
        for label in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
            label.set_fontproperties(tick_font)
    return ax


def _save(fig: Figure, base: Path, settings: dict) -> list[Path]:
    outputs: list[Path] = []
    transparent = settings["background"] == "transparent"
    for fmt in settings["formats"]:
        suffix = "tiff" if fmt == "tiff" else fmt
        target = base.with_suffix("." + suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        rc = {
            "svg.fonttype": "none" if settings["svg_text_mode"] == "editable" else "path",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
        with mpl.rc_context(rc):
            fig.savefig(
                target, format=suffix, dpi=settings["dpi"],
                transparent=transparent,
                facecolor="none" if transparent else "white",
                bbox_inches="tight" if settings["show_axes"] else None,
                pad_inches=0.04,
            )
        outputs.append(target)
    fig.clear()
    return outputs


def _title(ax, text: str, settings: dict) -> None:
    if settings["show_title"]:
        ax.set_title(text, color=settings["text_color"],
                     fontproperties=resolve_font(settings, settings["title_size"]))


def export_saed_annotation(
    image: np.ndarray,
    center: tuple[float, float],
    rings: list[dict],
    spots: list[dict],
    output_dir: str | Path,
    settings: dict,
) -> list[Path]:
    settings = validate_settings(settings)
    output = Path(output_dir)
    fig = _figure(settings, image.shape)
    ax = _axis(fig, image, settings)
    label_font = resolve_font(settings, settings["label_size"])
    cx, cy = center
    radius_style = settings["arrow_color"]
    for ring in rings:
        radius = float(ring["radius_px"])
        circle = Circle((cx, cy), radius, fill=False, edgecolor=radius_style,
                        linewidth=settings["arrow_linewidth"])
        ax.add_patch(circle)
        label = f"R{ring['id']}: {radius:.2f} px"
        if ring.get("d_nm") is not None:
            label += f"  d={ring['d_nm']:.4g} nm"
        ax.text(cx + radius / np.sqrt(2), cy - radius / np.sqrt(2), label,
                color=settings["text_color"], fontproperties=label_font,
                bbox=dict(facecolor=settings["label_background"],
                          alpha=settings["label_background_alpha"], pad=2))
    circle_radius = float(settings.get("circle_size", 8.0))
    for spot in spots:
        x, y = float(spot["x_px"]), float(spot["y_px"])
        ax.add_patch(Circle((x, y), circle_radius, fill=False,
                            edgecolor=settings["roi_verify_color"],
                            linewidth=settings["arrow_linewidth"]))
        ax.text(x + circle_radius + 2, y - circle_radius - 2, f"S{spot['id']}",
                color=settings["text_color"], fontproperties=label_font,
                bbox=dict(facecolor=settings["label_background"],
                          alpha=settings["label_background_alpha"], pad=1.5))
    ax.add_patch(Circle((cx, cy), max(2.0, circle_radius * 0.5), fill=False,
                        edgecolor=settings["roi_ok_color"],
                        linewidth=settings["arrow_linewidth"]))
    _title(ax, "SAED rings and diffraction spots", settings)
    return _save(fig, output / "saed_annotated", settings)


def _export_scalar(
    image: np.ndarray,
    base: Path,
    title: str,
    settings: dict,
    *,
    cmap: str = "gray",
    decorate: Callable | None = None,
) -> list[Path]:
    fig = _figure(settings, image.shape)
    ax = _axis(fig, image, settings, cmap=cmap)
    if decorate:
        decorate(ax)
    _title(ax, title, settings)
    return _save(fig, base, settings)


def export_hrtem_roi_bundle(
    arrays: dict[str, np.ndarray],
    metadata: dict,
    output_dir: str | Path,
    settings: dict,
) -> list[Path]:
    """Render a saved ROI session without rerunning FFT, ACF, or IFFT."""
    settings = validate_settings(settings)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    created += _export_scalar(arrays["roi"], output / "roi_original", "HRTEM ROI", settings)
    created += _export_scalar(arrays["fft_log"], output / "fft", "ROI FFT magnitude", settings, cmap="magma")

    label_font = resolve_font(settings, settings["label_size"])
    circle_radius = float(settings.get("circle_size", 8.0))
    def fft_marks(ax):
        for i, spot in enumerate(metadata["selected_spots"], 1):
            x, y = spot["x_px"], spot["y_px"]
            ax.add_patch(Circle((x, y), spot.get("radius_px", circle_radius), fill=False,
                                edgecolor=settings["arrow_color"],
                                linewidth=settings["arrow_linewidth"]))
            ax.text(x + circle_radius, y - circle_radius, f"P{i}",
                    color=settings["text_color"], fontproperties=label_font)
    created += _export_scalar(arrays["fft_log"], output / "fft_spots_annotated",
                              "Selected FFT spots", settings, cmap="magma", decorate=fft_marks)
    created += _export_scalar(arrays["mask"], output / "fft_mask", "Reproducible FFT mask", settings)
    created += _export_scalar(arrays["ifft"], output / "ifft_lattice", "IFFT enhanced lattice", settings)

    row = metadata["measurement"]
    def lattice_marks(ax):
        if row.get("d_fft_px") is None:
            return
        h, w = arrays["roi"].shape
        cx, cy = w / 2, h / 2
        theta = np.deg2rad(row["normal_angle_deg"])
        length = 0.2 * min(h, w)
        start = (cx - length * np.cos(theta), cy - length * np.sin(theta))
        end = (cx + length * np.cos(theta), cy + length * np.sin(theta))
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="<->",
                                    mutation_scale=settings["arrow_size"] * 2,
                                    color=settings["arrow_color"],
                                    linewidth=settings["arrow_linewidth"]))
        label = f"d = {row['d_fft_px']:.4g} px"
        if row.get("d_fft_nm") is not None:
            label = f"d = {row['d_fft_nm']:.4g} nm"
        ax.text(cx, cy - 0.27 * min(h, w), label, ha="center",
                color=settings["text_color"], fontproperties=label_font,
                bbox=dict(facecolor=settings["label_background"],
                          alpha=settings["label_background_alpha"], pad=2))
        if settings["show_roi"]:
            ax.add_patch(Rectangle((1, 1), w - 2, h - 2, fill=False,
                                   edgecolor=settings["roi_ok_color"],
                                   linewidth=settings["roi_linewidth"]))
    created += _export_scalar(arrays["ifft"], output / "lattice_spacing_annotated",
                              "IFFT lattice with measured direction", settings,
                              decorate=lattice_marks)
    return created
