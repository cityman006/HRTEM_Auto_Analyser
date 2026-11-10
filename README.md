# HRTEM Auto Analyser

HRTEM Auto Analyser is a local Windows application for HRTEM lattice-fringe measurement and SAED diffraction-pattern analysis. It reads DM3, DM4, TIFF, TIF, and PNG images and keeps original image data separate from generated presentation outputs.

## What is implemented

- Grid-based ROI analysis with configurable 1×1, 2×2, 4×4, and 8×8 layouts.
- Local FFT peak detection for the dominant fringe period.
- One-dimensional autocorrelation (ACF) cross-checking and OK/VERIFY quality status.
- Pixel-period and optional calibrated nm/px reporting in CSV and JSON summaries.
- Direction and spacing annotations on the input image.
- A Tk GUI and command-line interface.
- Output settings for installed system fonts, custom `.ttf`/`.otf` files, font sizes, colors, line widths, arrows, ROI boxes, labels, and background.
- PNG, TIFF, editable-text SVG, path-based SVG, and PDF export.
- Mixed raster/vector figures, annotation-only SVG layers, and vector-only curve plots.
- Saved analysis sessions that can be re-exported with new presentation settings without rerunning FFT or ACF.
- A separate SAED workflow with automatic direct-beam center, ring, and diffraction-spot detection; manual ring/spot correction; and CSV/JSON export.
- Multi-ROI HRTEM analysis with FFT spot selection, conjugate circular masks, reproducible IFFT reconstruction, and saved mask parameters.
- Separate GUI pages for HRTEM, SAED, and export settings.
- Interactive image workspaces with wheel zoom, toolbar pan, display-only brightness/contrast, ROI editing, FFT spot selection, SAED geometry editing, and bounded undo/redo.
- Annotation layers, per-object labels/colors, draggable labels, and automatic label placement.
- Local history with thumbnails, reopenable `.hrtemproj` projects, source hash checks, and named parameter/style presets.
- Batch file/folder processing with explicit dataset/frame selection, progress, cancellation, failed-item retries, and aggregate CSV output.

Complete HRTEM and FFT figures are mixed graphics: the microscopy image remains a raster image while labels, arrows, boxes, and plotted curves remain vector objects where the format supports them. Changing the file extension does not vectorize the microscopy pixels.

## Scientific limits

The program measures the dominant image-fringe period. It does not independently identify crystal-plane indices or prove a physical strain field. Without a reliable instrument calibration in nm/pixel, HRTEM results are reported in pixels only. Without a verified reciprocal calibration in nm⁻¹ per image pixel, SAED results report image-space ring radii and spot distances in pixels and omit d values. The software never invents a nanometre spacing.

## Requirements

Windows 10/11 with 64-bit Python 3.11 or a compatible CPython release. The tested project environment uses NumPy, SciPy, Matplotlib, tifffile, Pillow, imagecodecs, and ncempy (DM3/DM4). See [`requirements.txt`](requirements.txt) and [`requirements.lock.txt`](requirements.lock.txt).

## Install and start

From a checked-out project directory, create or reuse a project-local environment and install dependencies:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the GUI with [`START_HRTEM.cmd`](START_HRTEM.cmd). It resolves the project root from its own location, so the current working directory does not matter. [`REPAIR_HRTEM.cmd`](REPAIR_HRTEM.cmd) runs the project repair/diagnostic flow. The Python entry points are:

```powershell
.venv\Scripts\python.exe hrtem_spacing.py --self-test
.venv\Scripts\python.exe hrtem_spacing.py -i path\to\image.tif -o outputs\run_01 --grid 4x4
.venv\Scripts\python.exe hrtem_spacing.py --re-export-session outputs\run_01 -o outputs\run_01_reexport --export-settings config\export_settings.json
.venv\Scripts\python.exe saed_analysis.py -i path\to\pattern.dm4 -o outputs\saed_01
.venv\Scripts\python.exe saed_analysis.py -i path\to\pattern.tif -o outputs\saed_calibrated --reciprocal-calibration 0.0042
.venv\Scripts\python.exe hrtem_fft_tools.py -i path\to\image.tif -o outputs\roi_ifft --roi 200,300,512,512 --auto-spots 2
.venv\Scripts\python.exe hrtem_fft_tools.py -i path\to\image.tif -o outputs\roi_manual --roi 200,300,512,512 --pick-spots
```

The GUI's **Output Settings** dialog saves presentation preferences in the local `config/export_settings.json` file. This file is intentionally ignored because it is machine-specific; a fresh checkout uses validated defaults.

## Typical workflow

1. Select a DM3, DM4, TIFF, TIF, or PNG image.
2. Choose the grid and pixel-period range; enter nm/px only when it is known from the instrument or an independent calibration.
3. Run the analysis and inspect the annotated image, CSV, summary, FFT/ACF diagnostics, and saved session.
4. Open Output Settings to choose fonts, styles, DPI, dimensions, and formats.
5. Use **Re-export with current settings** to redraw an existing saved session without recalculating measurements.

For SAED, open the **SAED** page, run automatic ring/spot detection, then use **Manual correction** to add or delete detections before export. For ROI/IFFT, enter one or more `x,y,width,height` rectangles separated by semicolons, or leave the field blank to select rectangles interactively. The saved `hrtem_roi_result.json` and `hrtem_roi_session.npz` contain the selected FFT spots, mask radii, conjugate-mask policy, mask pixels, and reconstructed image.

Generated data and images should remain outside the public repository. The example paths above are intentionally local output paths.

## Interactive workspaces and batch processing

The main window now has **HRTEM 图像工作区**, **SAED 图像工作区**, **历史结果浏览器**, **批量分析**, and **打开项目文件** buttons. Existing analysis pages remain available. In the workspace, choose a mouse tool to create or edit ROIs, move FFT spots, adjust a mask radius, or correct SAED centers/rings/spots. Use **更新 IFFT** after editing FFT spots; measurement results are retained while the mask reconstruction is updated. Layer and presentation changes do not run FFT/ACF detection again.

Save a `.hrtemproj` project to retain selected source dataset/frame, source fingerprint, image buffers, objects, calibration, parameters, masks, reconstruction, and presentation settings. Reopening a project checks the source fingerprint and warns if the original is changed or missing; saved pixels remain available. Project files contain microscopy data and are intentionally excluded from Git. Presets are local to `config/presets/` and are also ignored.

The batch window accepts files or a folder and lets the user choose datasets/frames or queue all available image frames. Each item runs in an isolated subprocess. Cancel preserves completed work; retry creates a new attempt directory. Batch output contains per-item results plus `batch_summary.csv` and `batch_measurements.csv`. Source data are never overwritten.

See [the Chinese workspace guide](WORKBENCH_USER_GUIDE_CN.md) for exact controls and the checks below for reproducible validation. Very dense labels may still need manual adjustment. Real DM3 samples and synthetic multi-frame TIFF were validated locally; DM4 multi-frame indexing has reader-contract tests, but no real multi-frame DM4 specimen was available for acceptance. Private acceptance reports and source specimens are excluded from the repository. Crystal indexing and quantitative strain mapping are not implemented.

Generate reproducible synthetic examples without distributing research images:

```powershell
.venv\Scripts\python.exe tools\create_workbench_examples.py
```

## Development and version management

Run the self-test and synthetic-data validation tools below before committing a change. GUI checks require a desktop session. Keep measurement-algorithm changes separate from presentation changes and review them through a pull request.

```powershell
.venv\Scripts\python.exe hrtem_spacing.py --self-test
.venv\Scripts\python.exe tools\test_extension_core.py
.venv\Scripts\python.exe tools\test_extension_gui.py
.venv\Scripts\python.exe tools\test_workbench_model.py
.venv\Scripts\python.exe tools\test_workbench_storage.py
.venv\Scripts\python.exe tools\test_batch_workbench.py
.venv\Scripts\python.exe tools\test_workbench_integration.py
git status --short
git diff --cached --stat
```

The older `tools/test_vector_export.py`, `tools/validate_science.py`, and `tools/test_launchers.py` are local acceptance utilities with machine-specific fixtures or paths. They are not part of the clean-checkout test commands above; `test_extension_core.py` and the workbench tests provide synthetic export and regression coverage without private data.

For the recommended update flow, create a feature branch, run the checks, stage only reviewed source/docs/config-template changes, commit, push the branch, and open a pull request against `main`. To inspect or restore a previous version, use `git log --oneline --all` and `git show <commit>:<path>`; do not rewrite shared history.

## Repository status

This public repository does not include a license yet. Add one only after choosing an appropriate license for the project and its dependencies.
