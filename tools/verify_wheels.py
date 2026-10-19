import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'.downloads'/'wheels'
files=json.loads((folder/'official_manifest.json').read_text(encoding='utf-8'))
for entry in files:
    name=entry['filename']
    assert Path(name).name == name and name.endswith('.whl')
    assert urlparse(entry['url']).hostname == 'files.pythonhosted.org'
    path=folder/name
    assert path.stat().st_size == entry['size'], name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['digests']['sha256'], name
print(json.dumps(dict(verified_wheels=len(files), sha256_ok=True)))
