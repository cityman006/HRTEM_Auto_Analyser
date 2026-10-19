"""Generate and inspect font/vector samples without rerunning measurements."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from PIL import Image
from matplotlib import font_manager
import tifffile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from hrtem_export import DEFAULT, available_fonts, load_settings, resolve_font, validate_settings

ROOT=Path(__file__).resolve().parents[1]
SESSION=ROOT/'outputs'/'font_smoke_20261019_173954_980'
SUITE=ROOT/'outputs'/('font_vector_validation_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
SUITE.mkdir(parents=True,exist_ok=False)
IMAGE=ROOT/'位错分析.tif'
source_hash=hashlib.sha256(IMAGE.read_bytes()).hexdigest()
required={'Times New Roman','Arial'}
actual=set(available_fonts())
assert required.issubset(actual), f'Missing installed fonts: {required-actual}'
try:
    validate_settings(dict(DEFAULT,font_family='HRTEM Font That Is Not Installed'))
except ValueError as exc:
    assert 'not installed' in str(exc)
else: raise AssertionError('Missing font was silently accepted')
source_csv=hashlib.sha256((SESSION/'lattice_spacing_results.csv').read_bytes()).hexdigest()
source_summary=hashlib.sha256((SESSION/'summary.json').read_bytes()).hexdigest()
summary=[]
for family,mode,dpi,folder in [('Times New Roman','editable',300,'Times_New_Roman'),
                                ('Arial','path',600,'Arial')]:
    settings=deepcopy(DEFAULT)
    settings.update(font_family=family,svg_text_mode=mode,dpi=dpi,
                    formats=['png','tiff','svg','pdf'])
    settings=validate_settings(settings)
    resolved=Path(font_manager.findfont(resolve_font(settings,settings['label_size']),fallback_to_default=False)).resolve()
    assert resolved.is_file()
    config=SUITE/(folder+'_settings.json')
    config.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    target=SUITE/folder
    command=[sys.executable,str(ROOT/'hrtem_spacing.py'),'--re-export-session',str(SESSION),
             '-o',str(target),'--export-settings',str(config)]
    result=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=180)
    (SUITE/(folder+'_command.log')).write_text(result.stdout+'\nSTDERR:\n'+result.stderr,encoding='utf-8')
    assert result.returncode==0, result.stderr
    assert 'ROI 01' not in result.stdout and 'without FFT/ACF' in result.stdout
    assert hashlib.sha256((target/'lattice_spacing_results.csv').read_bytes()).hexdigest()==source_csv
    assert hashlib.sha256((target/'summary.json').read_bytes()).hexdigest()==source_summary
    with Image.open(target/'lattice_spacing_annotated.png') as im:
        im.verify()
    with Image.open(target/'lattice_spacing_annotated.png') as im:
        dimensions=im.size
    assert dimensions[0]>=int(180/25.4*dpi)-1
    tiff=tifffile.imread(target/'lattice_spacing_annotated.tiff')
    assert tiff.ndim==3 and tiff.shape[0]==dimensions[1] and tiff.shape[1]==dimensions[0]
    def tags(path):
        tree=ET.parse(path)
        return [elem.tag.split('}')[-1] for elem in tree.iter()]
    svg_tags=tags(target/'lattice_spacing_annotated.svg')
    overlay_tags=tags(target/'annotations_only.svg')
    curves_tags=tags(target/'curves_only.svg')
    assert 'image' in svg_tags and 'path' in svg_tags
    assert 'image' not in overlay_tags and 'path' in overlay_tags
    assert 'image' not in curves_tags and 'path' in curves_tags
    if mode=='editable':
        assert 'text' in svg_tags and 'text' in overlay_tags
        svg_content=(target/'lattice_spacing_annotated.svg').read_text(encoding='utf-8')
        assert family in svg_content
    else:
        assert 'text' not in svg_tags and 'text' not in overlay_tags
    for name in ('lattice_spacing_annotated.pdf','fft_acf_diagnostics.pdf','curves_only.pdf'):
        assert (target/name).read_bytes().startswith(b'%PDF-')
    summary.append(dict(font=family,font_file=str(resolved),svg_mode=mode,dpi=dpi,
                        png_pixels=dimensions,output=str(target),pdf_count=3,
                        csv_unchanged=True,summary_unchanged=True))
assert hashlib.sha256(IMAGE.read_bytes()).hexdigest()==source_hash
(SUITE/'validation.json').write_text(json.dumps(dict(samples=summary,original_hash_unchanged=True,
    source_csv_sha256=source_csv,source_summary_sha256=source_summary),indent=2,ensure_ascii=False),encoding='utf-8')
print(json.dumps(dict(validation_dir=str(SUITE),samples=summary),ensure_ascii=False),flush=True)
