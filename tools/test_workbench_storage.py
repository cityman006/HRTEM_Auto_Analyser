"""Persistence tests use real files and ZIP archives, without GUI interaction."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
import sys
ROOT=Path(__file__).resolve().parent
if not (ROOT/'workbench_storage.py').is_file(): ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))

import numpy as np

from history_browser import scan_history
from workbench_storage import (capture_source, load_project, save_project,
    save_preset, load_preset, list_presets, read_project_manifest)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "original.tif"
        self.source.write_bytes(b"original unchanged source")
        self.state = {"mode": "SAED", "source": capture_source(self.source),
                      "parameters": {"mask_radius": 7}, "rois": [[1, 2, 4, 5]],
                      "annotations": {"spot-1": {"color": "red", "text": "custom"}}}
        self.arrays = {"image": np.arange(120).reshape(10, 12),
                       "fft": np.array([1 + 3j, 2 + 5j]), "mask": np.ones((10, 12), dtype=np.uint8)}
        self.project = self.root / "projects" / "sample.hrtemproj"

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_relative_source_numeric_cache_and_annotations(self):
        save_project(self.project, self.state, self.arrays)
        self.assertEqual(read_project_manifest(self.project)["state"]["source"]["path"], os.path.join("..", "original.tif"))
        restored, arrays, warnings = load_project(self.project)
        self.assertEqual(warnings, [])
        self.assertEqual(restored, self.state)
        for name, values in self.arrays.items():
            np.testing.assert_array_equal(arrays[name], values)

    def test_changed_and_missing_source_preserve_hash_and_cache(self):
        baseline = self.state["source"]["sha256"]
        self.source.write_bytes(b"modified input")
        save_project(self.project, self.state, self.arrays)
        restored, arrays, warnings = load_project(self.project)
        self.assertEqual(restored["source"]["sha256"], baseline)
        self.assertIn("哈希不一致", warnings[0])
        self.source.unlink()
        _, _, warnings = load_project(self.project)
        self.assertIn("不存在", warnings[0])
        np.testing.assert_array_equal(arrays["image"], self.arrays["image"])

    def test_reject_schema_pickle_and_wrong_arrays(self):
        self.project.parent.mkdir()
        with zipfile.ZipFile(self.project, "w") as archive:
            archive.writestr("manifest.json", json.dumps({"schema": "untrusted", "version": 1}))
            archive.writestr("arrays.npz", b"")
        with self.assertRaisesRegex(ValueError, "schema"):
            load_project(self.project)
        with self.assertRaisesRegex(ValueError, "numeric"):
            save_project(self.project, self.state, {"image": np.array([{}], dtype=object)})
        with self.assertRaises(ValueError):
            save_project(self.project, {"source": {"sha256": "wrong"}}, self.arrays)

    def test_invalid_save_preserves_existing_file(self):
        save_project(self.project, self.state, self.arrays)
        initial = self.project.read_bytes()
        with self.assertRaises(ValueError):
            save_project(self.project, {"value": float("nan")}, self.arrays)
        self.assertEqual(initial, self.project.read_bytes())

    def test_corrupt_project_does_not_hide_other_history(self):
        save_project(self.project, self.state, self.arrays)
        (self.project.parent / "broken.hrtemproj").write_bytes(b"broken zip file")
        entries = scan_history(self.root)
        self.assertEqual(len(entries), 2)
        self.assertEqual(sum(bool(entry.error) for entry in entries), 1)
        invalid = self.root / "outputs" / "bad"
        invalid.mkdir(parents=True)
        (invalid / "saed_results.json").write_text("[]", encoding="utf-8")
        entries = scan_history(self.root)
        self.assertEqual(len(entries), 3)
        self.assertEqual(sum(bool(entry.error) for entry in entries), 2)

    def test_manifest_cache_mismatch_rejected(self):
        save_project(self.project, self.state, self.arrays)
        with zipfile.ZipFile(self.project) as archive:
            cache = archive.read("arrays.npz")
            manifest = json.loads(archive.read("manifest.json"))
        manifest["arrays"]["image"]["shape"] = [120]
        with zipfile.ZipFile(self.project, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("arrays.npz", cache)
        with self.assertRaisesRegex(ValueError, "do not match"):
            load_project(self.project)

    def test_preset_roundtrip_and_name_safety(self):
        save_preset(self.root, "电镜 A 200kV", {"mask_radius": 8}, {"font_family": "Arial"})
        self.assertEqual(list_presets(self.root), ["电镜 A 200kV"])
        self.assertEqual(load_preset(self.root, "电镜 A 200kV")["parameters"], {"mask_radius": 8})
        for name in ("../escape", "CON", "folder\\file", "unsafe.", ""):
            with self.assertRaises(ValueError):
                save_preset(self.root, name, {}, {})

    def test_metadata_history_includes_project_and_legacy(self):
        save_project(self.project, self.state, self.arrays)
        legacy = self.root / "outputs" / "test"
        legacy.mkdir(parents=True)
        (legacy / "saed_results.json").write_text(json.dumps({
            "source_metadata": {"source_path": str(self.source)},
            "calibration_nm_inv_per_px": 0.05}), encoding="utf-8")
        entries = scan_history(self.root)
        self.assertEqual(len(entries), 2)
        saed = next(entry for entry in entries if entry.kind == "SAED")
        self.assertEqual(saed.input, str(self.source))
        self.assertIn("0.05", saed.calibration)

    def test_real_model_roi_fft_and_automatic_snapshot_roundtrip(self):
        from workbench_model import WorkbenchModel
        yy, xx = np.mgrid[:96, :96]
        model = WorkbenchModel(20 + np.cos(2 * np.pi * xx / 8),
                               capture_source(self.source), params={"nm_per_pixel": 0.02})
        roi = model.add_object("roi", x=0, y=0, w=96, h=96)
        model.analyze_rois()
        model.state["output_dir"] = str(self.root / "outputs" / "run")
        save_project(self.project, model.state, model.arrays)
        state, arrays, warnings = load_project(self.project)
        self.assertEqual(warnings, [])
        self.assertEqual(state, model.state)
        self.assertTrue(any(key.startswith("auto:roi:") for key in arrays))
        self.assertTrue(np.iscomplexobj(arrays[f"roi:{roi['id']}:spectrum"]))
        for key in model.arrays:
            np.testing.assert_array_equal(model.arrays[key], arrays[key])
        history = scan_history(self.root)[0]
        self.assertEqual(history.calibration, "0.02 nm/px")
        self.assertEqual(history.parameters["mask_radius_px"], 6.0)
        self.assertEqual(history.output, self.root / "outputs" / "run")


if __name__ == "__main__":
    unittest.main(verbosity=2)
