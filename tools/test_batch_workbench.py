"""Focused public-behaviour checks for frame selection and batch recovery."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
import sys
ROOT=Path(__file__).resolve().parent
if not (ROOT/'batch_analysis.py').is_file(): ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
from unittest.mock import patch

import numpy as np
import tifffile

from batch_analysis import BatchRunner, DEFAULT_PARAMETERS
from batch_ui import parse_frames
from hrtem_export import DEFAULT
from microscopy_frames import list_datasets, list_frames, load_frame



class BatchChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="batch_checks_", dir=ROOT)
        self.folder = Path(self.temp.name)
        yy, xx = np.indices((128, 128))
        self.image = 120 + 40 * np.cos(2 * np.pi * xx / 8) + 2 * np.sin(yy / 11)
        self.settings = {**DEFAULT, "formats": ["png"], "dpi": 72, "width_mm": 60}

    def tearDown(self):
        self.temp.cleanup()

    def wait(self, runner):
        runner.thread.join(90)
        self.assertFalse(runner.running)

    def test_frames_axes_color_and_series(self):
        stack = self.folder / "stack.tif"
        tifffile.imwrite(stack, np.stack([self.image, self.image + 20, self.image + 70]), photometric="minisblack", metadata={"axes": "TYX"})
        self.assertEqual(list_datasets(stack)[0]["frames"], 3)
        self.assertEqual(len(list_frames(stack)), 3)
        pixels, meta = load_frame(stack, 0, 2)
        np.testing.assert_array_equal(pixels, self.image + 70)
        self.assertEqual(meta["frame"], 2)
        rgb = self.folder / "rgb.tif"
        color = np.stack([self.image, self.image + 10, self.image + 20], axis=-1).astype("uint8")
        tifffile.imwrite(rgb, color, photometric="rgb")
        self.assertEqual(len(list_frames(rgb)), 1)
        pixels, _ = load_frame(rgb, 0, 0)
        self.assertEqual(pixels.shape, self.image.shape)
        expected = color[..., 0] * .2126 + color[..., 1] * .7152 + color[..., 2] * .0722
        np.testing.assert_allclose(pixels, expected)
        series = self.folder / "series.tif"
        with tifffile.TiffWriter(series) as writer:
            writer.write(self.image, photometric="minisblack")
            writer.write(self.image[:64, :64], photometric="minisblack")
        self.assertEqual(len(list_datasets(series)), 2)
        self.assertEqual(load_frame(series, 1, 0)[0].shape, (64, 64))
        with self.assertRaises(IndexError):
            load_frame(stack, 0, 3)
        self.assertEqual(parse_frames("0,2-4", 5), [0, 2, 3, 4])

    def test_batch_success_failure_retry_and_csv(self):
        good, missing = self.folder / "good.tif", self.folder / "later.tif"
        tifffile.imwrite(good, self.image, photometric="minisblack")
        runner = BatchRunner(ROOT, [{"path": str(p), "dataset": 0, "frame": 0} for p in (good, missing)],
                             self.folder / "output", "HRTEM", DEFAULT_PARAMETERS, self.settings)
        runner.start(); self.wait(runner)
        self.assertEqual([i["status"] for i in runner.items], ["success", "failed"])
        first_output = runner.items[0]["output"]
        self.assertLess(abs(runner.items[0]["measurements"][0]["d_fft_px"] - 8), .1)
        self.assertIsNone(runner.items[0]["measurements"][0]["d_fft_nm"])
        tifffile.imwrite(missing, self.image, photometric="minisblack")
        runner.start(retry_failed=True); self.wait(runner)
        self.assertEqual([i["status"] for i in runner.items], ["success", "success"])
        self.assertEqual(runner.items[0]["output"], first_output)
        self.assertEqual(runner.items[1]["attempts"], 2)
        with (runner.output / "batch_measurements.csv").open(encoding="utf-8-sig") as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 2)
        aggregate = json.loads((runner.output / "batch_results.json").read_text(encoding="utf-8"))
        self.assertEqual(aggregate["items"][1]["status"], "success")

    def test_dm_four_dimensional_frame_mapping_contract(self):
        # Reader-contract test, not a claim of decoding a real DM4 fixture.
        data = np.arange(2 * 3 * 6 * 8, dtype=np.float32).reshape(2, 3, 6, 8)
        path = self.folder / "reader_contract.dm4"
        data.tofile(path)
        class Reader:
            numObjects = 2
            thumbnail = True
            zSize2, zSize, ySize, xSize = [1, 2], [1, 3], [2, 6], [2, 8]
            dataShape = [2, 4]
            scale, scaleUnit, origin = [1, 1, .2, .3, 1, 1], ["px", "px", "nm", "nm", "", ""], [0] * 6
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def getMemmap(self, index):
                if index != 0: raise AssertionError("Reader logical dataset mapping wrong")
                return np.memmap(path, dtype=np.float32, mode="r", shape=data.shape)
        with patch("ncempy.io.dm.fileDM", return_value=Reader()):
            self.assertEqual(list_datasets(path)[0]["dataset"], 1)
            self.assertEqual(len(list_frames(path)), 6)
            pixels, metadata = load_frame(path, 1, 4)
            np.testing.assert_array_equal(pixels, data[1, 1])
            self.assertEqual(metadata["pixelSize"], [.3, .2])
            self.assertFalse(metadata["calibration_applied"])

    def test_cancel_real_child_and_resume(self):
        source = self.folder / "image.tif"
        tifffile.imwrite(source, self.image, photometric="minisblack")
        runner = BatchRunner(ROOT, [{"path": str(source), "dataset": 0, "frame": 0}],
                             self.folder / "cancel", "ROI_IFFT", DEFAULT_PARAMETERS, self.settings)
        runner.start()
        deadline = time.monotonic() + 20
        while runner.process is None and runner.running and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(runner.process)
        runner.cancel(); self.wait(runner)
        self.assertEqual(runner.items[0]["status"], "cancelled")
        runner.start(retry_failed=True); self.wait(runner)
        self.assertEqual(runner.items[0]["status"], "success")
        self.assertTrue((Path(runner.items[0]["output"]) / "roi_01" / "hrtem_roi_session.npz").is_file())

    def test_saed_worker(self):
        yy, xx = np.indices((160, 160)); rr = np.hypot(xx - 80, yy - 80)
        image = 1 + 30 * np.exp(-(rr - 36) ** 2 / 3) + 20 * np.exp(-(rr - 60) ** 2 / 3)
        image += 80 * np.exp(-((xx - 116) ** 2 + (yy - 80) ** 2) / 3)
        source = self.folder / "saed.tif"
        tifffile.imwrite(source, image, photometric="minisblack")
        runner = BatchRunner(ROOT, [{"path": str(source), "dataset": 0, "frame": 0}],
                             self.folder / "saed", "SAED", DEFAULT_PARAMETERS, self.settings)
        runner.start(); self.wait(runner)
        self.assertEqual(runner.items[0]["status"], "success", runner.items[0].get("error"))
        self.assertGreaterEqual(runner.items[0]["summary"]["rings"], 2)
        self.assertTrue((Path(runner.items[0]["output"]) / "saed_session.npz").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
