import importlib
import json
import struct
import sys
result = dict(executable=sys.executable, version=sys.version, version_info=list(sys.version_info[:3]), bits=struct.calcsize('P')*8, prefix=sys.prefix, base_prefix=sys.base_prefix)
for name in ('venv', 'ensurepip', 'tkinter'):
    try:
        importlib.import_module(name)
        result[name] = True
    except Exception as exc:
        result[name] = False
        result[name + '_error'] = repr(exc)
print(json.dumps(result, ensure_ascii=True))
