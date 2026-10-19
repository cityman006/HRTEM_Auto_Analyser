import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ['MPLBACKEND'] = 'Agg'
os.environ['MPLCONFIGDIR'] = str(ROOT / '.cache' / 'matplotlib')
os.environ['PYTHONUTF8'] = '1'
os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.path.insert(0, str(ROOT))
import numpy as np
from PIL import Image
from hrtem_spacing import peak_from_fft, analyze_roi

LOG = ROOT / 'logs' / 'codex_repair.log'
def log(message):
    print(message, flush=True)
    with LOG.open('a', encoding='utf-8') as f:
        f.write(datetime.now().isoformat()+' '+message+'\n')
def run(args):
    log('RUN '+subprocess.list2cmdline(args))
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=240)
    log(json.dumps(dict(stdout=proc.stdout, stderr=proc.stderr, exit_code=proc.returncode), ensure_ascii=False))
    proc.check_returncode()
    return proc

expected = ROOT / '.venv' / 'Scripts' / 'python.exe'
assert Path(sys.executable).resolve() == expected.resolve()
run([sys.executable, str(ROOT/'hrtem_spacing.py'), '--help'])
run([sys.executable, str(ROOT/'hrtem_spacing.py'), '--self-test'])
# Additional independent acceptance criterion: known 7.25 px, <1% at several angles.
y, x = np.mgrid[:512, :512]
synthetic = []
for angle in (0., 27., 64., 90., 137.):
    theta = np.deg2rad(angle)
    rng = np.random.default_rng(20261019)
    z = 120 + 48*np.cos(2*np.pi*(x*np.cos(theta)+y*np.sin(theta))/7.25) + 10*rng.normal(size=x.shape)
    row, _ = analyze_roi(z, (0,0,512,512), 4, 15, 6)
    relative = abs(row['d_fft_px']-7.25)/7.25
    assert relative < .01, row
    synthetic.append(dict(angle_deg=angle, measured_px=row['d_fft_px'], relative_error=relative, acf_px=row['d_acf_px'], status=row['status']))
log('Synthetic <1% acceptance: '+json.dumps(synthetic))
original_hashes = json.loads((ROOT/'logs'/'original_image_hashes.json').read_text(encoding='utf-8-sig'))
if isinstance(original_hashes, dict): original_hashes = [original_hashes]
for entry in original_hashes:
    assert hashlib.sha256((ROOT / Path(entry['Path']).name).read_bytes()).hexdigest().upper() == entry['Hash']
source = ROOT / '位错分析.tif'
result = dict(synthetic=synthetic, original_analysis=False)
if source.exists():
    output = ROOT/'outputs'/('codex_validation_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    output.mkdir(parents=True, exist_ok=False)
    run([sys.executable, str(ROOT/'hrtem_spacing.py'), '-i', str(source), '-o', str(output), '--grid', '4x4'])
    summary = json.loads((output/'summary.json').read_text(encoding='utf-8'))
    with (output/'lattice_spacing_results.csv').open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == summary['count_rois'] == 16
    counts = dict(Counter(r['status'] for r in rows))
    assert counts.get('OK', 0) == summary['count_ok']
    assert summary['calibration_valid'] is False and summary['nm_per_pixel'] is None
    assert summary['median_spacing_nm_valid_rois'] is None
    for row in rows:
        assert not row['d_fft_nm'] and not row['d_acf_nm']
        for key, value in row.items():
            if value and key != 'status': assert math.isfinite(float(value)), (key, value)
    sizes = {}
    for name in ('lattice_spacing_annotated.png', 'fft_acf_diagnostics.png'):
        with Image.open(output/name) as image:
            sizes[name] = image.size
            image.verify()
        assert (output/name).stat().st_size > 1000
    result.update(original_analysis=True, output=str(output), summary=summary, statuses=counts, png_dimensions=sizes)
for entry in original_hashes:
    assert hashlib.sha256((ROOT / Path(entry['Path']).name).read_bytes()).hexdigest().upper() == entry['Hash']
result['original_hashes_unchanged'] = True
(ROOT/'logs'/'acceptance_results.json').write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
log('ACCEPTANCE PASSED '+json.dumps(result, ensure_ascii=False))
