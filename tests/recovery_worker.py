"""Isolated subprocess fixture. Never used by the production service entry point."""
import json
import os
from pathlib import Path
import sys
import uvicorn
from inkflow.web.app import create_app
from inkflow.web import durable

root, port = Path(sys.argv[1]), int(sys.argv[2])

def inject(point):
    arm = root / 'fault.json'
    if not arm.exists():
        return
    spec = json.loads(arm.read_text(encoding='utf-8'))
    context = durable._context.get() or {}
    if spec['point'] == point and (not spec.get('node') or spec['node'] == context.get('node')):
        arm.unlink()
        (root / 'crashed.txt').write_text(point, encoding='utf-8')
        os._exit(73)

durable.fault_hook = inject
uvicorn.run(create_app(root / 'config.toml', root / 'data', root), host='127.0.0.1', port=port, access_log=False, log_level='warning')
