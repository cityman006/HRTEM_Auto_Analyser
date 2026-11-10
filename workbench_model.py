"""Editable, undoable microscopy state; existing measurement algorithms are reused.

Array buffers are treated as immutable. History holds references to arrays and deep
copies of JSON state, so style edits do not copy large images or run measurements.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import uuid

import numpy as np

import hrtem_fft_tools as fft_tools
import saed_analysis as saed
from hrtem_export import DEFAULT, validate_settings
from microscopy_io import load_microscopy_image


DEFAULT_PARAMS = dict(min_spacing=4.0, max_spacing=15.0, min_peak_ratio=8.0,
                      nm_per_pixel=None, auto_spot_count=2, mask_radius_px=6.0,
                      calibration_nm_inv_per_px=None, max_rings=12, max_spots=48)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_params(params: dict | None) -> dict:
    result = deepcopy(DEFAULT_PARAMS)
    aliases = {"min_spacing_px": "min_spacing", "max_spacing_px": "max_spacing",
               "default_mask_radius_px": "mask_radius_px", "mask_radius": "mask_radius_px",
               "reciprocal_calibration": "calibration_nm_inv_per_px",
               "auto_spots": "auto_spot_count", "min_period": "min_spacing",
               "max_period": "max_spacing"}
    for key, value in (params or {}).items():
        result[aliases.get(key, key)] = value
    for key in DEFAULT_PARAMS:
        value = result[key]
        if key in ("nm_per_pixel", "calibration_nm_inv_per_px") and value in (None, ""):
            result[key] = None
            continue
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be a positive finite number.")
        if key in ("auto_spot_count", "max_rings", "max_spots"):
            if value != int(value) or value > 10000:
                raise ValueError(f"{key} must be an integer from 1 to 10000.")
            value = int(value)
        result[key] = value
    if result["max_spacing"] <= result["min_spacing"]:
        raise ValueError("max_spacing must be greater than min_spacing.")
    return result


class WorkbenchModel:
    HISTORY_LIMIT = 40

    def __init__(self, image: np.ndarray, source: dict, mode="hrtem", params=None, settings=None):
        image = np.asarray(image, dtype=float)
        if image.ndim != 2 or min(image.shape) < 2 or not np.isfinite(image).all():
            raise ValueError("Workbench requires a finite two-dimensional image.")
        if mode.lower() not in ("hrtem", "saed"):
            raise ValueError("mode must be hrtem or saed")
        self.state = dict(mode=mode.lower(), source=deepcopy(source),
                          params=validate_params(params), settings=validate_settings(settings or {}),
                          objects=[], layers={k: True for k in
                          ("image", "roi", "ring", "spot", "label", "arrow", "scale")},
                          brightness=0.0, contrast=1.0, view="image", active_roi=None,
                          results={}, auto_objects=[], auto_results={}, warnings=[],
                          ifft_dirty={}, parameters_changed=False)
        self.arrays = {"image": image.copy()}
        self._undo = []
        self._redo = []
        self._auto_snapshot = None
        source_path = source.get("path") or source.get("source_path")
        if source_path and Path(source_path).is_file():
            actual = file_sha256(source_path)
            expected = source.get("sha256") or source.get("source_sha256")
            if expected and expected != actual:
                self.state["warnings"].append("Input file hash differs from the saved source; saved pixels are retained.")
            self.state["source"].setdefault("sha256", expected or actual)

    def checkpoint(self):
        self._undo.append((deepcopy(self.state), dict(self.arrays)))
        self._undo = self._undo[-self.HISTORY_LIMIT:]
        self._redo.clear()

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append((deepcopy(self.state), dict(self.arrays)))
        self.state, self.arrays = self._undo.pop()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append((deepcopy(self.state), dict(self.arrays)))
        self.state, self.arrays = self._redo.pop()
        return True

    def _remember_auto(self):
        self.state["auto_objects"] = deepcopy(self.state["objects"])
        self.state["auto_results"] = deepcopy(self.state["results"])
        self.state["auto_params"] = deepcopy(self.state["params"])
        # Persisted keys allow restoring an automatic result after project reopening.
        for key in list(self.arrays):
            if key.startswith("auto:"):
                del self.arrays[key]
        self.arrays.update({"auto:" + key: value for key, value in list(self.arrays.items())
                            if key != "image"})
        self._auto_snapshot = True

    def restore_auto(self):
        if not self.state.get("auto_objects"):
            return False
        self.checkpoint()
        self.state["objects"] = deepcopy(self.state["auto_objects"])
        self.state["results"] = deepcopy(self.state.get("auto_results", {}))
        self.state["params"] = deepcopy(self.state.get("auto_params", self.state["params"]))
        self.state["parameters_changed"] = False
        self.state["ifft_dirty"] = {}
        self.arrays = {key: value for key, value in self.arrays.items()
                       if key == "image" or key.startswith("auto:")}
        self.arrays.update({key[5:]: value for key, value in list(self.arrays.items())
                            if key.startswith("auto:")})
        self.state["view"] = "image"
        self.state["active_roi"] = next((o["id"] for o in self.state["objects"] if o["kind"] == "roi"), None)
        return True

    def add_object(self, kind, **kwargs):
        if kind not in ("roi", "ring", "spot", "center", "arrow", "scale"):
            raise ValueError(f"Unsupported annotation kind: {kind}")
        obj = {"id": uuid.uuid4().hex[:12], "kind": kind, "view": "image",
               "x": 0.0, "y": 0.0, "label": "", "color": "#00ffc8", **kwargs}
        if "label" not in kwargs and kind in ("roi", "ring", "spot"):
            prefix = {"roi": "ROI", "ring": "R", "spot": "S"}[kind]
            obj["label"] = f"{prefix}{1 + sum(o['kind'] == kind for o in self.state['objects'])}"
        obj["id"] = str(obj["id"])
        if any(item["id"] == obj["id"] for item in self.state["objects"]):
            raise ValueError("Annotation ID must be unique.")
        for key in ("x", "y", "w", "h", "radius", "label_x", "label_y", "x2", "y2"):
            if key in obj:
                obj[key] = float(obj[key])
                if not math.isfinite(obj[key]):
                    raise ValueError(f"{key} must be finite.")
        if kind == "roi" and (obj.get("w", 0) <= 0 or obj.get("h", 0) <= 0):
            raise ValueError("ROI width and height must be positive.")
        if kind in ("ring", "spot") and obj.get("radius", 1) <= 0:
            raise ValueError("Radius must be positive.")
        self.state["objects"].append(obj)
        return obj

    def delete(self, ids):
        ids = set(ids)
        if any(o["id"] in ids and o["kind"] == "center" for o in self.state["objects"]):
            raise ValueError("The diffraction center is required; drag it to correct its position.")
        self.checkpoint()
        for obj in list(self.state["objects"]):
            if obj["id"] in ids and obj["kind"] == "roi":
                self.invalidate_roi(obj["id"])
        self.state["objects"] = [obj for obj in self.state["objects"] if obj["id"] not in ids]

    def duplicate(self, ids):
        self.checkpoint()
        copies = []
        for obj in list(self.state["objects"]):
            if obj["id"] not in ids or obj["kind"] == "center":
                continue
            clone = deepcopy(obj)
            clone.pop("id")
            kind = clone.pop("kind")
            clone["x"] += 8
            clone["y"] += 8
            if "label_x" in clone: clone["label_x"] += 8
            if "label_y" in clone: clone["label_y"] += 8
            if kind == "roi":
                clone["x"] = min(clone["x"], self.arrays["image"].shape[1] - clone["w"])
                clone["y"] = min(clone["y"], self.arrays["image"].shape[0] - clone["h"])
            copies.append(self.add_object(kind, **clone))
        return copies

    def invalidate_roi(self, roi_id):
        self.state["results"].pop(roi_id, None)
        self.state.setdefault("ifft_dirty", {}).pop(roi_id, None)
        self.state["objects"] = [o for o in self.state["objects"] if o["view"] != "fft:" + roi_id]
        for key in list(self.arrays):
            if key.startswith(f"roi:{roi_id}:"):
                del self.arrays[key]
        if self.state["view"] != "image" and roi_id in self.state["view"]:
            self.state["view"] = "image"

    def analyze_saed(self):
        params = validate_params(self.state["params"])
        result, profile = saed.analyze_saed(self.arrays["image"],
            calibration_nm_inv_per_px=params["calibration_nm_inv_per_px"],
            max_rings=params["max_rings"], max_spots=params["max_spots"])
        self.checkpoint()
        self.state["params"] = params
        self.state["parameters_changed"] = False
        self.state["results"]["saed"] = result
        self.arrays["radial_profile"] = profile
        self._load_saed_objects(result)
        self._remember_auto()
        return result

    def _load_saed_objects(self, result):
        self.state["objects"] = [obj for obj in self.state["objects"]
                                  if obj["kind"] not in ("center", "ring", "spot") or obj["view"] != "image"]
        cx, cy = result["center_px"]
        self.add_object("center", x=cx, y=cy, label="Center", color="#ffcc33")
        h, w = self.arrays["image"].shape
        for index, ring in enumerate(result["rings"]):
            angle = -math.pi/4 + (index % 4)*.3
            lx = float(np.clip(cx + ring["radius_px"]*math.cos(angle), 2, max(2, w-24)))
            ly = float(np.clip(cy + ring["radius_px"]*math.sin(angle), 12, h-2))
            self.add_object("ring", x=cx, y=cy, radius=ring["radius_px"],
                            label=f"R{ring['id']}", label_x=lx, label_y=ly,
                            strength=ring["strength"], manual=ring["manual"])
        for spot in result["spots"]:
            self.add_object("spot", x=spot["x_px"], y=spot["y_px"], radius=4.0,
                            label=f"S{spot['id']}", manual=spot["manual"])

    def recompute_saed(self):
        params = validate_params(self.state["params"])
        calibration = params["calibration_nm_inv_per_px"]
        objects = [obj for obj in self.state["objects"] if obj["view"] == "image"]
        centers = [obj for obj in objects if obj["kind"] == "center"]
        if len(centers) != 1:
            raise ValueError("SAED requires exactly one diffraction center.")
        center = (float(centers[0]["x"]), float(centers[0]["y"]))
        image = self.arrays["image"]
        h, w = image.shape
        if not (0 <= center[0] < w and 0 <= center[1] < h):
            raise ValueError("Diffraction center is outside the image.")
        rings, spots = [], []
        for obj in objects:
            if obj["kind"] == "ring":
                radius = float(obj["radius"])
                if not math.isfinite(radius) or radius <= 0:
                    raise ValueError("Ring radius must be finite and positive.")
                obj["x"], obj["y"] = center
                row = saed._ring_record(radius, len(rings) + 1, calibration, obj.get("strength", 0))
                row["manual"] = bool(obj.get("manual", True))
                rings.append(row)
            elif obj["kind"] == "spot":
                x, y = float(obj["x"]), float(obj["y"])
                if not (0 <= x < w and 0 <= y < h):
                    raise ValueError("SAED spot is outside the image.")
                intensity = float(image[min(h-1, round(y)), min(w-1, round(x))])
                spots.append(saed._spot_record(x, y, center, intensity, len(spots)+1,
                                              calibration, manual=bool(obj.get("manual", True))))
        result = {"schema": saed.SCHEMA, "image_shape": list(image.shape), "center_px": list(center),
                  "calibration_nm_inv_per_px": calibration, "calibration_valid": calibration is not None,
                  "unit_note": "Explicit reciprocal calibration." if calibration else
                  "Uncalibrated reciprocal-image pixels; no physical d values.", "rings": rings, "spots": spots}
        previous = self.state["results"].get("saed", {})
        if previous.get("center_px") != list(center) or "radial_profile" not in self.arrays:
            self.arrays["radial_profile"] = saed.radial_profile(image, center)
        self.state["results"]["saed"] = result
        return result

    def _saed_geometry_matches(self):
        saved = self.state["results"].get("saed")
        if not saved or saved.get("calibration_nm_inv_per_px") != self.state["params"].get("calibration_nm_inv_per_px"):
            return False
        objects = [o for o in self.state["objects"] if o["view"] == "image"]
        centers = [[o["x"], o["y"]] for o in objects if o["kind"] == "center"]
        return (centers == [saved["center_px"]]
                and [o["radius"] for o in objects if o["kind"] == "ring"] == [r["radius_px"] for r in saved["rings"]]
                and [[o["x"], o["y"]] for o in objects if o["kind"] == "spot"] == [[s["x_px"], s["y_px"]] for s in saved["spots"]])

    def analyze_rois(self, ids=None):
        params = validate_params(self.state["params"])
        rois = [o for o in self.state["objects"] if o["kind"] == "roi" and (ids is None or o["id"] in ids)]
        if not rois:
            raise ValueError("Draw at least one ROI before analysis.")
        pending = []
        h, w = self.arrays["image"].shape
        for obj in rois:
            x, y, rw, rh = [int(round(obj[k])) for k in ("x", "y", "w", "h")]
            if x < 0 or y < 0 or rw < 16 or rh < 16 or x+rw > w or y+rh > h:
                raise ValueError("ROI must be inside the image and at least 16 x 16 pixels.")
            arrays, metadata = fft_tools.analyze_roi_ifft(self.arrays["image"], (x, y, rw, rh),
                min_spacing=params["min_spacing"], max_spacing=params["max_spacing"],
                min_peak_ratio=params["min_peak_ratio"], nm_per_pixel=params["nm_per_pixel"],
                auto_spot_count=params["auto_spot_count"], mask_radius_px=params["mask_radius_px"])
            arrays["spectrum"] = fft_tools._spectrum(arrays["roi"])[0]
            metadata["source_metadata"] = deepcopy(self.state["source"])
            metadata["source_path"] = self.state["source"].get("path", self.state["source"].get("source_path", ""))
            pending.append((obj, arrays, metadata))
        self.checkpoint()
        self.state["params"] = params
        self.state["parameters_changed"] = False
        for obj, arrays, metadata in pending:
            rid = obj["id"]
            self.invalidate_roi(rid)
            for key, value in arrays.items():
                self.arrays[f"roi:{rid}:{key}"] = value
            self.state["results"][rid] = metadata
            self._load_fft_spots(rid, metadata)
            self.state["active_roi"] = rid
        self._remember_auto()
        return {obj["id"]: meta for obj, _, meta in pending}

    def _load_fft_spots(self, roi_id, metadata):
        for index, spot in enumerate(metadata["selected_spots"], 1):
            self.add_object("spot", view="fft:" + roi_id, x=spot["x_px"], y=spot["y_px"],
                            radius=spot["radius_px"], label=f"F{index}", manual=spot.get("manual", False))

    def update_ifft(self, roi_id):
        key = f"roi:{roi_id}:"
        if roi_id not in self.state["results"] or key + "roi" not in self.arrays:
            raise ValueError("Analyze this ROI before computing IFFT.")
        objects = [obj for obj in self.state["objects"] if obj["view"] == "fft:" + roi_id and obj["kind"] == "spot"]
        if not objects:
            raise ValueError("Select at least one FFT spot.")
        fft_log = self.arrays[key + "fft_log"]
        spots = []
        for obj in objects:
            values = [float(obj[k]) for k in ("x", "y", "radius")]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("FFT spot coordinates and radius must be finite.")
            # spot_from_xy rounds for intensity sampling; clip sampling separately at edge.
            spot = fft_tools.spot_from_xy(fft_log.shape, *values)
            x, y, _ = values
            spot["intensity_log"] = float(fft_log[min(fft_log.shape[0]-1, round(y)), min(fft_log.shape[1]-1, round(x))])
            spots.append(spot)
        spectrum = self.arrays.get(key + "spectrum")
        if spectrum is None:
            spectrum = fft_tools._spectrum(self.arrays[key + "roi"])[0]
        mask = fft_tools.build_conjugate_mask(fft_log.shape, spots)
        restored = fft_tools.inverse_fft(spectrum, mask)
        self.checkpoint()
        self.arrays.update({key + "spectrum": spectrum, key + "mask": mask.astype(np.uint8), key + "ifft": restored})
        self.state["results"][roi_id]["selected_spots"] = spots
        self.state["results"][roi_id]["ifft_dirty"] = False
        self.state.setdefault("ifft_dirty", {})[roi_id] = False
        return restored

    def mark_ifft_dirty(self, roi_id):
        if roi_id in self.state["results"]:
            self.state["results"][roi_id]["ifft_dirty"] = True
            self.state.setdefault("ifft_dirty", {})[roi_id] = True

    def export_results(self, output: Path):
        if self.state.get("parameters_changed"):
            raise ValueError("Analysis parameters changed; run analysis before exporting results.")
        if any(self.state.get("ifft_dirty", {}).values()):
            raise ValueError("FFT selection changed. Recompute IFFT before exporting.")
        if self.state["mode"] == "hrtem":
            rois = [o for o in self.state["objects"] if o["kind"] == "roi"]
            if not rois or any(o["id"] not in self.state["results"] for o in rois):
                raise ValueError("Analyze every ROI before exporting measurement results.")
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        settings = validate_settings(self.state["settings"])
        paths = []
        if self.state["mode"] == "saed":
            result = self.state["results"]["saed"] if self._saed_geometry_matches() else self.recompute_saed()
            result["source_path"] = self.state["source"].get("path", self.state["source"].get("source_path", ""))
            paths += saed.save_saed_session(self.arrays["image"], self.arrays["radial_profile"], result,
                                            output / "saed", settings, self.state["source"])
        else:
            for index, obj in enumerate([o for o in self.state["objects"] if o["kind"] == "roi"], 1):
                rid = obj["id"]
                if rid not in self.state["results"]:
                    continue
                if self.state["results"][rid].get("ifft_dirty"):
                    raise ValueError("FFT selection changed. Recompute IFFT before exporting.")
                arrays = {key: self.arrays[f"roi:{rid}:{key}"] for key in ("roi", "fft_log", "mask", "ifft")}
                paths += fft_tools.save_roi_session(arrays, self.state["results"][rid], output / f"roi_{index:02d}", settings)
        (output / "workbench_state.json").write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        paths += self.export_annotations(output)
        return paths

    def export_annotations(self, output: Path):
        from annotation_export import _figure, _save
        from matplotlib.patches import Circle, Rectangle
        from hrtem_export import resolve_font
        settings = validate_settings(self.state["settings"])
        paths = []
        views = ["image"] + sorted({o["view"] for o in self.state["objects"] if o["view"].startswith("fft:")})
        for index, view in enumerate(views):
            pixels = self.arrays["image"] if view == "image" else self.arrays.get("roi:" + view[4:] + ":fft_log")
            if pixels is None:
                continue
            fig = _figure(settings, pixels.shape)
            ax = fig.add_axes([0, 0, 1, 1])
            h, w = pixels.shape
            if self.state["layers"]["image"]:
                low, high = np.percentile(pixels, [0.2, 99.8])
                span = max(float(high-low), 1e-12) / max(float(self.state["contrast"]), 0.01)
                middle = (high+low)/2 - float(self.state["brightness"]) * (high-low)
                ax.imshow(pixels, cmap="gray" if view == "image" else "magma", origin="upper", vmin=middle-span/2, vmax=middle+span/2)
            ax.set_xlim(-.5, w-.5); ax.set_ylim(h-.5, -.5); ax.set_axis_off()
            for obj in self.state["objects"]:
                kind = obj["kind"]
                if obj["view"] != view or not self.state["layers"].get(kind, True):
                    continue
                x, y, color = obj["x"], obj["y"], obj.get("color", settings["arrow_color"])
                if kind == "roi":
                    ax.add_patch(Rectangle((x, y), obj["w"], obj["h"], fill=False, edgecolor=color, linewidth=settings["roi_linewidth"]))
                elif kind in ("ring", "spot"):
                    ax.add_patch(Circle((x, y), obj.get("radius", 4), fill=False, edgecolor=color, linewidth=settings["roi_linewidth"]))
                elif kind == "center":
                    ax.plot([x], [y], marker="+", color=color)
                elif kind in ("arrow", "scale"):
                    ax.annotate("", (obj.get("x2", x+obj.get("w", 30)), obj.get("y2", y)), (x, y),
                                arrowprops=dict(arrowstyle="->" if kind == "arrow" else "|-|", color=color, lw=settings["arrow_linewidth"]))
                if self.state["layers"]["label"] and obj.get("label"):
                    ax.text(obj.get("label_x", x+5), obj.get("label_y", y-5), obj["label"], color=color,
                            fontproperties=resolve_font(settings, settings["label_size"]))
            paths += _save(fig, output / ("workbench_annotation" if view == "image" else f"workbench_fft_{index:02d}"), settings)
        return paths

    @classmethod
    def from_session(cls, path, params=None, settings=None):
        path = Path(path)
        folder = path if path.is_dir() else path.parent
        if (folder / "saed_results.json").is_file():
            result = json.loads((folder / "saed_results.json").read_text(encoding="utf-8-sig"))
            with np.load(folder / "saed_session.npz", allow_pickle=False) as saved:
                image, profile = saved["image"].copy(), saved["radial_profile"].copy()
            source = deepcopy(result.get("source_metadata", {}))
            source.setdefault("path", result.get("source_path", ""))
            effective = {**(params or {}), "calibration_nm_inv_per_px": result.get("calibration_nm_inv_per_px")}
            model = cls(image, source, "saed", effective, settings)
            model.state["results"]["saed"] = result
            model.arrays["radial_profile"] = profile
            model._load_saed_objects(result)
        elif (folder / "hrtem_roi_result.json").is_file():
            result = json.loads((folder / "hrtem_roi_result.json").read_text(encoding="utf-8-sig"))
            with np.load(folder / "hrtem_roi_session.npz", allow_pickle=False) as saved:
                arrays = {key: saved[key].copy() for key in ("roi", "fft_log", "mask", "ifft")}
            source = deepcopy(result.get("source_metadata", {}))
            source.setdefault("path", result.get("source_path", ""))
            effective = {**(params or {}), **result.get("analysis_parameters", {}), "nm_per_pixel": result.get("calibration_nm_per_pixel")}
            # Retain saved ROI pixels even when original data has moved or changed.
            model = cls(arrays["roi"], source, "hrtem", effective, settings)
            obj = model.add_object("roi", x=0, y=0, w=arrays["roi"].shape[1], h=arrays["roi"].shape[0], label="Saved ROI")
            model.state["results"][obj["id"]] = result
            model.arrays.update({f"roi:{obj['id']}:{key}": value for key, value in arrays.items()})
            model._load_fft_spots(obj["id"], result)
            model.state["active_roi"] = obj["id"]
            model.state["warnings"].append("Legacy ROI session opens its saved crop; measurement retains original image coordinates.")
        elif (folder / "analysis_session.json").is_file():
            result = json.loads((folder / "analysis_session.json").read_text(encoding="utf-8-sig"))
            source_path = Path(result["source"])
            if not source_path.is_file():
                raise FileNotFoundError("Legacy HRTEM session requires its original image: " + str(source_path))
            expected = result.get("source_sha256")
            if expected and file_sha256(source_path) != expected:
                raise ValueError("Source SHA256 differs; cannot attach old measurements to changed source pixels.")
            image, metadata = load_microscopy_image(source_path)
            model = cls(image, {**metadata, "path": str(source_path), "sha256": expected}, "hrtem",
                        {**(params or {}), "nm_per_pixel": result.get("nm_per_pixel")}, settings)
            model.state["results"]["legacy"] = result
            for index, row in enumerate(result["rows"], 1):
                roi = row.get("roi") or [row.get(k) for k in ("x", "y", "width", "height")]
                if len(roi) == 4 and all(v is not None for v in roi):
                    model.add_object("roi", x=roi[0], y=roi[1], w=roi[2], h=roi[3], label=f"ROI {index}")
        else:
            roi_folders = sorted(folder.glob("roi_*/hrtem_roi_result.json"))
            if roi_folders:
                return cls._from_roi_run(roi_folders, params, settings)
            if (folder / "saed" / "saed_results.json").is_file():
                return cls.from_session(folder / "saed", params, settings)
            raise ValueError("No supported saved SAED or HRTEM session was found.")
        model._remember_auto()
        return model

    @classmethod
    def _from_roi_run(cls, result_paths, params=None, settings=None):
        entries = []
        for path in result_paths:
            result = json.loads(path.read_text(encoding="utf-8-sig"))
            with np.load(path.parent / "hrtem_roi_session.npz", allow_pickle=False) as saved:
                arrays = {key: saved[key].copy() for key in ("roi", "fft_log", "mask", "ifft")}
            x, y, w, h = (int(v) for v in result["measurement"]["roi"])
            if min(x, y) < 0 or min(w, h) < 1 or arrays["roi"].shape != (h, w):
                raise ValueError("Saved ROI coordinates and cached image shape do not match.")
            entries.append((result, arrays, (x, y, w, h)))
        first = entries[0][0]
        source = deepcopy(first.get("source_metadata", {}))
        source.setdefault("path", first.get("source_path", ""))
        width = max(x+w for _, _, (x, y, w, h) in entries)
        height = max(y+h for _, _, (x, y, w, h) in entries)
        if width*height > 100_000_000:
            raise ValueError("Saved ROI layout exceeds 100 million pixels; open individual ROI sessions.")
        source_path = source.get("path")
        expected = source.get("sha256") or source.get("source_sha256")
        trustworthy = bool(source_path and expected and Path(source_path).is_file()
                           and file_sha256(source_path) == expected)
        image = None
        if trustworthy:
            try:
                image, _ = load_microscopy_image(source_path)
            except (ValueError, OSError):
                image = None
        if image is None or image.shape[0] < height or image.shape[1] < width:
            image = np.zeros((height, width), dtype=float)
            warning = "Legacy ROI run restored from saved crops. Pixels outside saved ROIs are blank; analyze only saved image areas."
        else:
            image = image.copy()
            warning = ""
        for _, arrays, (x, y, w, h) in entries:
            image[y:y+h, x:x+w] = arrays["roi"]
        effective = {**(params or {}), **first.get("analysis_parameters", {}),
                     "nm_per_pixel": first.get("calibration_nm_per_pixel")}
        model = cls(image, source, "hrtem", effective, settings)
        for index, (result, arrays, (x, y, w, h)) in enumerate(entries, 1):
            obj = model.add_object("roi", x=x, y=y, w=w, h=h, label=f"ROI {index}")
            rid = obj["id"]
            model.state["results"][rid] = result
            model.arrays.update({f"roi:{rid}:{key}": value for key, value in arrays.items()})
            model._load_fft_spots(rid, result)
            model.state["active_roi"] = rid
        if warning:
            model.state["warnings"].append(warning)
            model.state["cached_roi_only"] = True
        model._remember_auto()
        return model
