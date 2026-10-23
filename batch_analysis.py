"""Isolated batch workers, resumable queue state, and aggregate scientific results."""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback

DEFAULT_PARAMETERS = {
    "min_spacing": 4.0, "max_spacing": 15.0, "min_peak_ratio": 8.0,
    "nm_per_pixel": None, "reciprocal_calibration": None, "max_rings": 12,
    "max_spots": 48, "mask_radius": 6.0, "auto_spots": 2,
}


def validate_parameters(params: dict) -> dict:
    aliases={'mask_radius_px':'mask_radius','auto_spot_count':'auto_spots',
             'calibration_nm_inv_per_px':'reciprocal_calibration'}
    values = {**DEFAULT_PARAMETERS, **{aliases.get(k,k):v for k,v in params.items()}}
    import math
    for key in ("min_spacing", "max_spacing", "min_peak_ratio", "mask_radius"):
        values[key] = float(values[key])
        if not math.isfinite(values[key]) or values[key] <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if values["min_spacing"] >= values["max_spacing"]:
        raise ValueError("Minimum spacing must be less than maximum spacing")
    for key in ("max_rings", "max_spots", "auto_spots"):
        value = float(values[key])
        if not math.isfinite(value) or not value.is_integer() or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        values[key] = int(value)
    for key in ("nm_per_pixel", "reciprocal_calibration"):
        if values[key] in (None, ""):
            values[key] = None
        else:
            values[key] = float(values[key])
            if not math.isfinite(values[key]) or values[key] <= 0:
                raise ValueError(f"{key} must be finite and positive or blank")
    return values


def run_item(job: dict) -> dict:
    """Run inside its own process; use unchanged analysis functions."""
    import matplotlib
    matplotlib.use("Agg")
    import numpy as np
    from microscopy_frames import load_frame
    from hrtem_export import validate_settings
    params = validate_parameters(job["parameters"])
    settings = validate_settings(job["export_settings"])
    item = job["item"]
    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    image, source_metadata = load_frame(item["path"], int(item["dataset"]), int(item["frame"]))
    source_metadata["batch_parameters"] = params
    mode = job["mode"]
    measurements = []
    if mode == "SAED":
        from saed_analysis import analyze_saed, save_saed_session
        result, profile = analyze_saed(image, calibration_nm_inv_per_px=params["reciprocal_calibration"],
                                      max_rings=params["max_rings"], max_spots=params["max_spots"])
        result["source_path"] = str(Path(item["path"]).resolve())
        save_saed_session(image, profile, result, output, settings, source_metadata)
        measurements = [{"kind": "ring", **row} for row in result["rings"]]
        measurements += [{"kind": "spot", **row} for row in result["spots"]]
        summary = {"rings": len(result["rings"]), "spots": len(result["spots"]),
                   "center_px": result["center_px"]}
    elif mode == "ROI_IFFT":
        from hrtem_fft_tools import analyze_roi_ifft, save_roi_session
        rois = params.get("rois") or [(0, 0, image.shape[1], image.shape[0])]
        summaries = []
        for index, roi in enumerate(rois, 1):
            arrays, result = analyze_roi_ifft(image, tuple(roi), min_spacing=params["min_spacing"],
                max_spacing=params["max_spacing"], min_peak_ratio=params["min_peak_ratio"],
                nm_per_pixel=params["nm_per_pixel"], auto_spot_count=params["auto_spots"],
                mask_radius_px=params["mask_radius"])
            result["source_path"] = str(Path(item["path"]).resolve())
            result["source_metadata"] = source_metadata
            folder = output / f"roi_{index:02d}"
            save_roi_session(arrays, result, folder, settings)
            measurements.append({"kind": "roi", "roi_id": index, **result["measurement"]})
            summaries.append({"roi_id": index, "output": str(folder), "status": result["measurement"]["status"]})
        summary = {"workflow": "HRTEM_ROI_IFFT", "count_rois": len(summaries), "rois": summaries}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    elif mode == "HRTEM":
        from hrtem_spacing import choose_rois, analyze_roi, write_csv
        from hrtem_export import export_all, save_session
        import tifffile
        roi_strings = [",".join(map(str, roi)) for roi in params.get("rois", [])] or None
        rois = choose_rois(image.shape, params.get("grid", "1x1"), roi_strings)
        rows, best_diag, best_roi = [], None, None
        for index, roi in enumerate(rois, 1):
            row, diag = analyze_roi(image, roi, params["min_spacing"], params["max_spacing"], params["min_peak_ratio"])
            row["roi_id"] = index
            for kind in ("fft", "acf"):
                value = row.get(f"d_{kind}_px")
                row[f"d_{kind}_nm"] = (value * params["nm_per_pixel"]
                                            if value is not None and params["nm_per_pixel"] is not None else None)
            rows.append(row)
            if diag is not None and row["status"] == "OK" and (best_diag is None or
                    row["peak_to_median"] > best_diag["fft"]["peak_to_median"]):
                best_diag, best_roi = diag, roi
        write_csv(rows, output / "lattice_spacing_results.csv")
        export_all(image, rows, params["nm_per_pixel"], best_diag, best_roi, output, settings)
        # A selected frame is saved so the legacy re-export loader cannot
        # silently reopen the wrong frame from the original multidimensional file.
        frame_path = output / "selected_frame.tif"
        tifffile.imwrite(frame_path, image, photometric="minisblack")
        save_session(output, frame_path, rows, params["nm_per_pixel"], best_diag, best_roi)
        measurements = [{"kind": "roi", **row} for row in rows]
        valid = [r["d_fft_px"] for r in rows if r["status"] == "OK"]
        summary = {"workflow": "HRTEM", "input": item["path"], "count_rois": len(rows),
                   "count_ok": len(valid), "median_spacing_px_valid_rois": float(np.median(valid)) if valid else None,
                   "nm_per_pixel": params["nm_per_pixel"], "calibration_valid": params["nm_per_pixel"] is not None}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        raise ValueError(f"Unknown workflow: {mode}")
    (output / "input_frame.json").write_text(json.dumps(source_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"summary": summary, "measurements": measurements, "source_metadata": source_metadata}


def write_reports(output: Path, items: list[dict], parameters: dict, mode: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    record = {"schema": "hrtem-batch-v1", "updated_at": datetime.now().astimezone().isoformat(),
              "mode": mode, "parameters": parameters, "items": items}
    temporary = output / "batch_results.json.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output / "batch_results.json")
    columns = ["id", "path", "dataset", "frame", "status", "attempts", "duration_seconds", "output", "error"]
    with (output / "batch_summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader(); writer.writerows(items)
    flattened = []
    for item in items:
        base = {k: item.get(k) for k in ("id", "path", "dataset", "frame", "status", "output")}
        for row in item.get("measurements", []):
            flattened.append({**base, **{k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}})
    columns = list(dict.fromkeys(key for row in flattened for key in row)) or ["id", "path", "status", "kind"]
    with (output / "batch_measurements.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader(); writer.writerows(flattened)


class BatchRunner:
    """Single bounded queue. Cancellation terminates only this runner's child."""
    def __init__(self, root: Path, items: list[dict], output: Path, mode: str,
                 parameters: dict, settings: dict, callback=None, python: str | None = None):
        self.root, self.output, self.mode = Path(root), Path(output), mode
        self.parameters = validate_parameters(parameters)
        self.settings = copy.deepcopy(settings)
        self.items = [{**copy.deepcopy(item), "id": index, "status": "pending", "attempts": 0,
                       "duration_seconds": 0, "error": "", "output": ""}
                      for index, item in enumerate(items, 1)]
        candidate = self.root / ".venv" / "Scripts" / "python.exe"
        self.python = python or (str(candidate) if candidate.is_file() else sys.executable)
        self.callback = callback
        self.cancel_event = threading.Event()
        self.thread = None
        self.process = None
        self._lock = threading.Lock()

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def _emit(self, kind, item=None):
        if self.callback:
            self.callback(kind, copy.deepcopy(item))

    def start(self, retry_failed=False):
        if self.running:
            raise RuntimeError("Batch is already running")
        if retry_failed:
            for item in self.items:
                if item["status"] in ("failed", "cancelled"):
                    item["status"] = "pending"
                    item.pop("measurements", None)
                    item["error"] = ""
        self.cancel_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()

    def _run(self):
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            for item in self.items:
                if item["status"] != "pending":
                    continue
                if self.cancel_event.is_set():
                    item["status"] = "cancelled"
                    self._emit("item", item)
                    continue
                item["attempts"] += 1
                folder = self.output / f"item_{item['id']:04d}" / f"attempt_{item['attempts']:02d}"
                folder.mkdir(parents=True, exist_ok=True)
                item.update(status="running", output=str(folder), error="")
                self._emit("item", item)
                started = time.monotonic()
                try:
                    job = {"item": item, "output": str(folder), "mode": self.mode,
                           "parameters": self.parameters, "export_settings": self.settings}
                    job_path = folder / "job.json"
                    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
                    with (folder / "worker.log").open("w", encoding="utf-8") as log:
                        with self._lock:
                            if self.cancel_event.is_set():
                                item["status"] = "cancelled"
                                continue
                            self.process = subprocess.Popen([self.python, str(Path(__file__).resolve()),
                                "--worker", str(job_path)], cwd=str(self.root), stdout=log,
                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        code = self.process.wait()
                    response_path = folder / "worker_result.json"
                    response = json.loads(response_path.read_text(encoding="utf-8")) if response_path.is_file() else {}
                    if self.cancel_event.is_set():
                        item.update(status="cancelled", error="Cancelled by user; partial output retained")
                    elif code != 0 or "error" in response:
                        item.update(status="failed", error=response.get("error", f"Worker exit {code}; see worker.log"))
                    else:
                        item.update(status="success", **response)
                except Exception as exc:
                    item.update(status="failed", error=str(exc))
                finally:
                    with self._lock:
                        self.process = None
                    item["duration_seconds"] = round(time.monotonic() - started, 3)
                    write_reports(self.output, self.items, self.parameters, self.mode)
                    self._emit("item", item)
            write_reports(self.output, self.items, self.parameters, self.mode)
        except Exception as exc:
            self._emit("error", str(exc))
        finally:
            self._emit("done", str(self.output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args()
    job = json.loads(args.worker.read_text(encoding="utf-8-sig"))
    destination = Path(job["output"]) / "worker_result.json"
    try:
        result = run_item(job)
        status = 0
    except Exception as exc:
        traceback.print_exc()
        result, status = {"error": f"{type(exc).__name__}: {exc}"}, 1
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
