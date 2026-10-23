"""Scientific state/regression checks; run with the existing project Python."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench_model import WorkbenchModel, file_sha256, validate_params
import hrtem_fft_tools as fft_tools
import saed_analysis


def lattice():
    y, x = np.indices((128, 128))
    return 2 + np.cos(2*np.pi*x/8) + .18*np.cos(2*np.pi*y/11)


def diffraction():
    y, x = np.indices((192, 192))
    r = np.hypot(x-95, y-97)
    image = 2+100*np.exp(-r*r/8)
    for radius in (30, 55, 75):
        image += 9*np.exp(-((r-radius)/1.6)**2)
    for sx, sy in ((150, 97), (40, 97), (95, 42), (95, 152)):
        image += 35*np.exp(-((x-sx)**2+(y-sy)**2)/3)
    return image


class ModelTests(unittest.TestCase):
    def test_validation(self):
        for bad in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                validate_params({"nm_per_pixel": bad})
            with self.assertRaises(ValueError):
                validate_params({"calibration_nm_inv_per_px": bad})
        with self.assertRaises(ValueError):
            validate_params({"min_spacing": 15, "max_spacing": 4})
        self.assertIsNone(validate_params({})["nm_per_pixel"])

    def test_history_and_roi_invalidation(self):
        m = WorkbenchModel(lattice(), {})
        m.checkpoint()
        roi = m.add_object("roi", x=0, y=0, w=128, h=128)
        self.assertTrue(m.undo())
        self.assertEqual(m.state["objects"], [])
        self.assertTrue(m.redo())
        rid = roi["id"]
        m.analyze_rois()
        self.assertIn(rid, m.state["results"])
        m.checkpoint()
        m.invalidate_roi(rid)
        self.assertNotIn(rid, m.state["results"])
        self.assertFalse(any(o["view"] == "fft:"+rid for o in m.state["objects"]))
        m.undo()
        self.assertIn(rid, m.state["results"])
        m.delete([rid])
        self.assertNotIn(rid, m.state["results"])
        m.undo()
        self.assertIn(rid, m.state["results"])

    def test_legacy_measurement_unchanged_and_ifft_edit(self):
        image = lattice()
        _, baseline = fft_tools.analyze_roi_ifft(image, (0, 0, 128, 128))
        m = WorkbenchModel(image, {})
        roi = m.add_object("roi", x=0, y=0, w=128, h=128)
        rid = roi["id"]
        m.analyze_rois()
        self.assertEqual(m.state["results"][rid]["measurement"], baseline["measurement"])
        automatic_mask = m.arrays[f"roi:{rid}:mask"].copy()
        spot = next(o for o in m.state["objects"] if o["view"] == "fft:"+rid)
        spot["radius"] = 2
        with patch.object(fft_tools, "analyze_roi_ifft", side_effect=AssertionError("remeasurement")):
            with patch.object(fft_tools, "_spectrum", side_effect=AssertionError("uncached FFT")):
                m.update_ifft(rid)
        self.assertEqual(m.state["results"][rid]["measurement"], baseline["measurement"])
        self.assertFalse(np.array_equal(m.arrays[f"roi:{rid}:mask"], automatic_mask))
        self.assertTrue(m.restore_auto())
        np.testing.assert_array_equal(m.arrays[f"roi:{rid}:mask"], automatic_mask)
        self.assertEqual(m.state["results"][rid]["selected_spots"], baseline["selected_spots"])

    def test_saed_center_edit_recomputes_physical_results(self):
        m = WorkbenchModel(diffraction(), {}, "saed")
        result = m.analyze_saed()
        self.assertGreaterEqual(len(result["rings"]), 3)
        self.assertGreaterEqual(len(result["spots"]), 4)
        self.assertTrue(all(s["d_nm"] is None for s in result["spots"]))
        center = next(o for o in m.state["objects"] if o["kind"] == "center")
        center["x"] += 3
        m.state["params"]["calibration_nm_inv_per_px"] = .02
        result = m.recompute_saed()
        for s in result["spots"]:
            distance = np.hypot(s["x_px"]-center["x"], s["y_px"]-center["y"])
            self.assertAlmostEqual(s["distance_px"], distance)
            self.assertAlmostEqual(s["d_nm"], 1/(distance*.02))
        self.assertTrue(all(o["x"] == center["x"] for o in m.state["objects"] if o["kind"] == "ring"))

    def test_saved_arrays_and_hash_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.tiff"
            tifffile.imwrite(source, diffraction())
            m = WorkbenchModel(diffraction(), {"path": str(source)}, "saed", settings={"dpi": 72, "width_mm": 60})
            m.analyze_saed()
            m.export_results(root / "out")
            expected = m.state["source"]["sha256"]
            self.assertEqual(file_sha256(source), expected)
            tifffile.imwrite(source, diffraction()+1)
            loaded = WorkbenchModel.from_session(root / "out" / "saed")
            self.assertTrue(loaded.state["warnings"])
            self.assertEqual(loaded.state["source"]["sha256"], expected)
            np.testing.assert_array_equal(loaded.arrays["image"], m.arrays["image"])

    def test_export_formats_styles_and_no_remeasurement(self):
        m = WorkbenchModel(lattice(), {}, settings={"formats": ["png", "tiff", "svg", "pdf"], "dpi": 72, "width_mm": 60})
        roi = m.add_object("roi", x=0, y=0, w=128, h=128, label="custom label", color="#ff0000")
        rid = roi["id"]
        m.analyze_rois()
        before = json.dumps(m.state["results"], sort_keys=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(fft_tools, "analyze_roi_ifft", side_effect=AssertionError("remeasurement")):
                m.export_results(root)
                m.state["settings"]["font_family"] = "DejaVu Sans"
                m.state["objects"][0]["color"] = "#00ff00"
                m.export_results(root / "restyled")
            for extension in ("png", "tiff", "svg", "pdf"):
                self.assertGreater((root / ("workbench_annotation."+extension)).stat().st_size, 100)
            svg = (root / "workbench_annotation.svg").read_text(encoding="utf-8")
            self.assertIn("<image", svg)
            self.assertIn("<text", svg)
            self.assertIn("custom label", svg)
            self.assertIn("#ff0000", svg)
            self.assertEqual(before, json.dumps(m.state["results"], sort_keys=True))
            imported = WorkbenchModel.from_session(root / "roi_01")
            self.assertEqual(imported.state["results"][imported.state["active_roi"]]["measurement"], m.state["results"][rid]["measurement"])

    def test_dirty_parameters_guard_and_saved_calibration(self):
        m = WorkbenchModel(lattice(), {}, params={"nm_per_pixel": .025}, settings={"dpi": 72, "width_mm": 60})
        roi = m.add_object("roi", x=0, y=0, w=128, h=128)
        rid = roi["id"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError): m.export_results(root)
            m.analyze_rois()
            m.state["parameters_changed"] = True
            with self.assertRaises(ValueError): m.export_results(root)
            m.analyze_rois()
            self.assertFalse(m.state["parameters_changed"])
            m.mark_ifft_dirty(rid)
            with self.assertRaises(ValueError): m.export_results(root)
            m.update_ifft(rid)
            self.assertFalse(m.state["ifft_dirty"][rid])
            m.export_results(root)
            imported = WorkbenchModel.from_session(root / "roi_01", params={"nm_per_pixel": None})
            self.assertEqual(imported.state["params"]["nm_per_pixel"], .025)
            m.state["params"]["nm_per_pixel"] = .5
            m.state["parameters_changed"] = True
            m.restore_auto()
            self.assertEqual(m.state["params"]["nm_per_pixel"], .025)
            self.assertFalse(m.state["parameters_changed"])

    def test_center_delete_rejected(self):
        m = WorkbenchModel(diffraction(), {}, "saed")
        m.analyze_saed()
        center = next(o for o in m.state["objects"] if o["kind"] == "center")
        with self.assertRaises(ValueError): m.delete([center["id"]])

    def test_saed_style_export_does_not_recompute(self):
        m = WorkbenchModel(diffraction(), {}, "saed", settings={"dpi": 72, "width_mm": 60})
        m.analyze_saed()
        m.state["objects"][1]["label"] = "Edited label"
        m.state["objects"][1]["color"] = "#ff0000"
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(saed_analysis, "analyze_saed", side_effect=AssertionError("remeasurement")):
                with patch.object(saed_analysis, "radial_profile", side_effect=AssertionError("radial recalculation")):
                    m.export_results(Path(directory))

    def test_multi_roi_run_preserves_all_results(self):
        m = WorkbenchModel(lattice(), {}, settings={"dpi": 72, "width_mm": 60})
        m.add_object("roi", x=0, y=0, w=64, h=64)
        m.add_object("roi", x=64, y=64, w=64, h=64)
        m.analyze_rois()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            m.export_results(root)
            loaded = WorkbenchModel.from_session(root)
            self.assertEqual(len(loaded.state["results"]), 2)
            self.assertEqual(len([o for o in loaded.state["objects"] if o["kind"] == "roi"]), 2)
            self.assertTrue(loaded.state["cached_roi_only"])
            for obj in loaded.state["objects"]:
                if obj["kind"] == "roi":
                    x,y,w,h = [int(obj[k]) for k in ("x","y","w","h")]
                    np.testing.assert_array_equal(loaded.arrays["image"][y:y+h,x:x+w], lattice()[y:y+h,x:x+w])


if __name__ == "__main__":
    unittest.main(verbosity=2)
