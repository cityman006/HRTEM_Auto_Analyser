#!/usr/bin/env python3
"""Deterministic functional tests for the SAED and ROI/IFFT extension."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hrtem_export import DEFAULT, available_fonts, resolve_font, validate_settings
from hrtem_fft_tools import (analyze_roi_ifft, reexport_roi_session,
                             save_roi_session, spot_from_xy)
from saed_analysis import (add_ring, add_spot, analyze_saed, delete_item,
                           reexport_saed, save_saed_session)


def synthetic_saed(size=256):
    yy, xx = np.indices((size, size))
    cx, cy = 129.2, 125.7
    radius = np.hypot(xx-cx, yy-cy)
    image = 7 + 170*np.exp(-(radius/3.0)**2)
    for r, amp in ((38, 42), (73, 30), (101, 22)):
        image += amp*np.exp(-0.5*((radius-r)/1.5)**2)
    for angle in np.deg2rad([0, 60, 120, 180, 240, 300]):
        sx, sy = cx+59*np.cos(angle), cy-59*np.sin(angle)
        image += 85*np.exp(-0.5*(((xx-sx)/1.6)**2+((yy-sy)/1.6)**2))
    rng = np.random.default_rng(20261021)
    return image + rng.normal(0, 1.2, image.shape), (cx, cy)


def synthetic_hrtem(size=256, period=8.0):
    yy, xx = np.indices((size, size))
    theta = np.deg2rad(27)
    phase = 2*np.pi*(xx*np.cos(theta)+yy*np.sin(theta))/period
    rng = np.random.default_rng(1234)
    return 100 + 28*np.sin(phase) + 3*rng.normal(size=(size, size))


def main():
    image, truth = synthetic_saed()
    result, profile = analyze_saed(image, max_rings=6, max_spots=20)
    assert np.linalg.norm(np.asarray(result['center_px'])-truth) < 2.5, result['center_px']
    detected = [r['radius_px'] for r in result['rings']]
    for expected in (38, 73, 101):
        assert min(abs(value-expected) for value in detected) <= 3, detected
    assert all(r['d_nm'] is None for r in result['rings'])
    calibrated, _ = analyze_saed(image, calibration_nm_inv_per_px=0.005,
                                 max_rings=6, max_spots=20)
    assert all(r['d_nm'] is not None for r in calibrated['rings'])
    before = len(result['rings'])
    add_ring(result, 88.5); assert len(result['rings']) == before + 1
    delete_item(result, 'ring', next(r['id'] for r in result['rings'] if r['manual']))
    assert len(result['rings']) == before
    add_spot(result, 10, 20); assert result['spots'][-1]['manual']
    delete_item(result, 'spot', result['spots'][-1]['id'])

    lattice = synthetic_hrtem()
    fft_center = lattice.shape[0] // 2
    kx = np.cos(np.deg2rad(27)) / 8.0
    ky = np.sin(np.deg2rad(27)) / 8.0
    spot_x = int(round(fft_center + kx*lattice.shape[1]))
    spot_y = int(round(fft_center + ky*lattice.shape[0]))
    selected = [spot_from_xy(lattice.shape, spot_x, spot_y, 5)]
    arrays, metadata = analyze_roi_ifft(
        lattice, (0, 0, 256, 256), min_spacing=5, max_spacing=12,
        min_peak_ratio=3, selected_spots=selected)
    assert abs(metadata['measurement']['d_fft_px']-8.0) < 0.15
    assert metadata['measurement']['d_fft_nm'] is None
    assert arrays['mask'][spot_y, spot_x] == 1
    assert arrays['mask'][2*fft_center-spot_y, 2*fft_center-spot_x] == 1
    assert float(np.std(arrays['ifft'])) > 0.1

    settings = deepcopy(DEFAULT)
    settings.update(formats=['png', 'tiff', 'svg', 'pdf'], dpi=100, width_mm=90,
                    svg_text_mode='editable', circle_size=7)
    settings = validate_settings(settings)
    with tempfile.TemporaryDirectory(prefix='hrtem_extension_') as folder:
        out = Path(folder)
        saed_out = out/'saed'
        files = save_saed_session(image, profile, result, saed_out, settings)
        assert len(files) == 4
        svg = (saed_out/'saed_annotated.svg').read_text(encoding='utf-8')
        assert '<image' in svg and '<text' in svg
        assert (saed_out/'saed_annotated.pdf').read_bytes().startswith(b'%PDF')
        assert (saed_out/'saed_annotated.tiff').stat().st_size > 1000
        original_json = json.loads((saed_out/'saed_results.json').read_text(encoding='utf-8'))
        reexport_saed(saed_out, out/'saed_reexport', settings)
        assert json.loads((out/'saed_reexport'/'saed_results.json').read_text(encoding='utf-8'))['rings'] == original_json['rings']

        roi_out = out/'roi'
        files = save_roi_session(arrays, metadata, roi_out, settings)
        assert len(files) == 24
        with np.load(roi_out/'hrtem_roi_session.npz') as saved:
            mask_before = saved['mask'].copy()
        reexport_roi_session(roi_out, out/'roi_reexport', settings)
        with np.load(out/'roi_reexport'/'hrtem_roi_session.npz') as saved:
            assert np.array_equal(mask_before, saved['mask'])
        fft_svg = (roi_out/'fft_spots_annotated.svg').read_text(encoding='utf-8')
        assert '<image' in fft_svg and '<text' in fft_svg

    fonts = set(available_fonts())
    tested = []
    for name in ('Times New Roman', 'Arial'):
        if name in fonts:
            font_settings = deepcopy(DEFAULT); font_settings['font_family'] = name
            prop = resolve_font(validate_settings(font_settings), 10)
            assert name.casefold() in prop.get_name().casefold()
            tested.append(name)
    print(json.dumps({
        'saed_center_error_px': float(np.linalg.norm(np.asarray(result['center_px'])-truth)),
        'saed_ring_radii_px': detected,
        'saed_spots': len(result['spots']),
        'hrtem_period_px': metadata['measurement']['d_fft_px'],
        'fonts_verified': tested,
        'formats_verified': ['PNG', 'TIFF', 'SVG', 'PDF'],
        'reexport_without_analysis': True,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
