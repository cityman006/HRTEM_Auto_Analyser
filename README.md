# HRTEM Auto Analyser

HRTEM Auto Analyser is a local Windows application for measuring dominant lattice-fringe periods in high-resolution transmission electron microscopy (HRTEM) images. It analyzes TIFF, TIF, and PNG images and keeps the original image data separate from generated presentation outputs.

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

Complete HRTEM and FFT figures are mixed graphics: the microscopy image remains a raster image while labels, arrows, boxes, and plotted curves remain vector objects where the format supports them. Changing the file extension does not vectorize the microscopy pixels.

## Scientific limits

The program measures the dominant image-fringe period. It does not independently identify crystal-plane indices or prove a physical strain field. Without a reliable instrument calibration in nm/pixel, results are reported in pixels only; the software does not invent a nanometre spacing.

## Requirements

Windows 10/11 with 64-bit Python 3.11 or a compatible CPython release. The tested project environment uses NumPy, SciPy, Matplotlib, tifffile, Pillow, and imagecodecs. See [`requirements.txt`](requirements.txt) and [`requirements.lock.txt`](requirements.lock.txt).

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
```

The GUI's **Output Settings** dialog saves presentation preferences in the local `config/export_settings.json` file. This file is intentionally ignored because it is machine-specific; a fresh checkout uses validated defaults.

## Typical workflow

1. Select a TIFF, TIF, or PNG image.
2. Choose the grid and pixel-period range; enter nm/px only when it is known from the instrument or an independent calibration.
3. Run the analysis and inspect the annotated image, CSV, summary, FFT/ACF diagnostics, and saved session.
4. Open Output Settings to choose fonts, styles, DPI, dimensions, and formats.
5. Use **Re-export with current settings** to redraw an existing saved session without recalculating measurements.

Generated data and images should remain outside the public repository. The example paths above are intentionally local output paths.

## Development and version management

Run the existing self-test and the vector/export validation tools before committing a change. Keep measurement-algorithm changes separate from presentation changes and review them through a pull request.

```powershell
.venv\Scripts\python.exe hrtem_spacing.py --self-test
.venv\Scripts\python.exe tools\test_vector_export.py
git status --short
git diff --cached --stat
```

For the recommended update flow, create a feature branch, run the checks, stage only reviewed source/docs/config-template changes, commit, push the branch, and open a pull request against `main`. To inspect or restore a previous version, use `git log --oneline --all` and `git show <commit>:<path>`; do not rewrite shared history.

## Repository status

This public repository does not include a license yet. Add one only after choosing an appropriate license for the project and its dependencies.
