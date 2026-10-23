#!/usr/bin/env python3
"""Generate reproducible, non-research synthetic extension examples."""
from copy import deepcopy
import argparse
from pathlib import Path
import sys

import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hrtem_export import DEFAULT, validate_settings
from hrtem_fft_tools import analyze_roi_ifft, save_roi_session
from saed_analysis import analyze_saed, save_saed_session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    settings = deepcopy(DEFAULT)
    settings.update(formats=['png','tiff','svg','pdf'], dpi=120, width_mm=100,
                    svg_text_mode='editable', circle_size=7)
    settings = validate_settings(settings)

    size = 256; yy, xx = np.indices((size, size)); cx, cy = 129.2, 125.7
    radius = np.hypot(xx-cx, yy-cy)
    saed = 7 + 170*np.exp(-(radius/3)**2)
    for r, amp in ((38,42),(73,30),(101,22)):
        saed += amp*np.exp(-0.5*((radius-r)/1.5)**2)
    for angle in np.deg2rad([0,60,120,180,240,300]):
        sx, sy = cx+59*np.cos(angle), cy-59*np.sin(angle)
        saed += 85*np.exp(-0.5*(((xx-sx)/1.6)**2+((yy-sy)/1.6)**2))
    saed += np.random.default_rng(20261021).normal(0,1.2,saed.shape)
    tifffile.imwrite(args.output/'synthetic_saed_input.tiff', saed.astype('float32'))
    result, profile = analyze_saed(saed, max_rings=6, max_spots=20)
    save_saed_session(saed, profile, result, args.output/'saed', settings,
                      {'source_format':'synthetic','calibration_applied':False})

    phase = 2*np.pi*(xx*np.cos(np.deg2rad(27))+yy*np.sin(np.deg2rad(27)))/8
    hrtem = 100+28*np.sin(phase)+3*np.random.default_rng(1234).normal(size=(size,size))
    tifffile.imwrite(args.output/'synthetic_hrtem_input.tiff', hrtem.astype('float32'))
    arrays, metadata = analyze_roi_ifft(hrtem, (0,0,size,size), min_spacing=5,
                                        max_spacing=12, min_peak_ratio=3)
    save_roi_session(arrays, metadata, args.output/'hrtem_roi_ifft', settings)
    print(args.output)


if __name__ == '__main__':
    main()
