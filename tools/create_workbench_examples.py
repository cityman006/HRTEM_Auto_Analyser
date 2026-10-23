"""Generate synthetic, reproducible examples; no research images are included."""
from datetime import datetime
import json
from pathlib import Path
import sys
import time

import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hrtem_export import DEFAULT
from workbench_model import WorkbenchModel
from workbench_storage import capture_source, load_project, save_project


def main():
    output = ROOT / 'outputs' / ('workbench_examples_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True, exist_ok=False)
    y, x = np.indices((256, 256))
    rng = np.random.default_rng(42)
    lattice = 100 + 30*np.sin(2*np.pi*(x*np.cos(.45)+y*np.sin(.45))/8)
    lattice += rng.normal(0, 1, lattice.shape)
    radius = np.hypot(x-128, y-127)
    diffraction = 5 + 150*np.exp(-(radius/3)**2)
    for r in (35, 70, 98):
        diffraction += 25*np.exp(-((radius-r)/1.5)**2)
    for angle in np.deg2rad([0, 60, 120, 180, 240, 300]):
        sx, sy = 128+55*np.cos(angle), 127+55*np.sin(angle)
        diffraction += 90*np.exp(-((x-sx)**2+(y-sy)**2)/4)
    settings = {**DEFAULT, 'formats': ['png', 'tiff', 'svg', 'pdf'],
                'dpi': 130, 'width_mm': 100, 'svg_text_mode': 'editable',
                'font_family': 'Arial'}
    summary = {}
    for mode, image in [('hrtem', lattice), ('saed', diffraction)]:
        source = output / ('synthetic_' + mode + '.tif')
        tifffile.imwrite(source, image.astype(np.float32))
        model = WorkbenchModel(image, capture_source(source), mode=mode,
                               params={'min_peak_ratio': 3}, settings=settings)
        started = time.perf_counter()
        if mode == 'hrtem':
            for index, (rx, ry) in enumerate([(0, 0), (96, 96)], 1):
                model.add_object('roi', x=rx, y=ry, w=160, h=160, label=f'ROI {index}',
                                 label_x=rx+5, label_y=ry+14)
            model.analyze_rois()
        else:
            model.analyze_saed()
            # A deterministic readable placement retained in the project file.
            for index, obj in enumerate(o for o in model.state['objects'] if o['kind'] == 'spot'):
                angle = np.arctan2(obj['y']-127, obj['x']-128)
                obj['label_x'] = 128+80*np.cos(angle)
                obj['label_y'] = 127+80*np.sin(angle)
                obj['label'] = f'S{index+1}'
        model.state['created_at'] = datetime.now().astimezone().isoformat()
        model.state['elapsed_seconds'] = time.perf_counter()-started
        model.add_object('scale', view='image', x=15, y=230, x2=65, y2=230,
                         label='50 px', label_x=15, label_y=220)
        destination = output / mode
        model.state['output_dir'] = str(destination)
        model.export_results(destination)
        project = destination / 'example.hrtemproj'
        save_project(project, model.state, model.arrays)
        restored, arrays, warnings = load_project(project)
        assert not warnings
        assert np.array_equal(arrays['image'], model.arrays['image'])
        for ext in settings['formats']:
            assert (destination / ('workbench_annotation.'+ext)).stat().st_size > 0
        svg = (destination / 'workbench_annotation.svg').read_text(encoding='utf-8')
        assert '<image' in svg and '<text' in svg and '<path' in svg
        summary[mode] = {'project': str(project), 'files': len(list(destination.rglob('*'))),
                         'elapsed_seconds': restored['elapsed_seconds'], 'project_roundtrip': True}
    (output/'examples.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(output), **summary}, indent=2))


if __name__ == '__main__':
    main()
