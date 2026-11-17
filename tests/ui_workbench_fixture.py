"""Isolated UI fixture. Run only after: conda activate openai_agents.

Reuses the recovery tests' provider double, temporary database and process cleanup.
No real provider, workspace, credentials or output directory is used.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from test_ai_recovery import AIRecoveryTests


class UIFixture(AIRecoveryTests):
    def start(self):
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name('ui_provider_worker.py')), str(self.root), str(self.port)],
            stdout=self.log, stderr=self.log,
        )
        self.until(self.ready)


def main():
    if os.environ.get('CONDA_DEFAULT_ENV') != 'openai_agents':
        raise SystemExit('Activate openai_agents before running the UI fixture.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='.inkflow/ui-verify/fixture.json')
    args = parser.parse_args()
    fixture = UIFixture()
    try:
        fixture.setUp()
        root = fixture.root
        (root / 'browser.mdx').write_bytes(b'# P6 Browser\r\n\r\n<X />\r\n')
        doc = fixture.req('POST', '/documents/open', {'path': str(root / 'browser.mdx')})
        run = fixture.req('POST', '/runs', {
            'request_id': uuid4().hex, 'workflow_id': 'full-check', 'raw_text': doc['text'],
            'editor_source': {key: doc[key] for key in ('document_id', 'revision')},
        }, 201, doc['document_id'])
        fixture.until(lambda: fixture.req('GET', '/runs/' + run['id'],
                      document_id=doc['document_id'])['status'] == 'failed')
        location = Path(args.output)
        location.parent.mkdir(parents=True, exist_ok=True)

        def publish(sequence):
            location.write_text(json.dumps({
                'isolated_ui_fixture': True, 'root': str(root), 'port': fixture.port,
                'doc': doc, 'run': run['id'], 'seq': sequence, 'pid': fixture.process.pid,
            }), encoding='utf-8')

        publish(0)
        print(f'Isolated UI fixture ready: {location}', flush=True)
        while True:
            control = root / 'control.json'
            if control.exists():
                value = json.loads(control.read_text(encoding='utf-8'))
                control.unlink()
                if value['action'] == 'stop':
                    break
                if value['action'] == 'restart':
                    fixture.kill()
                    fixture.start()
                    publish(value['seq'])
            time.sleep(.1)
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
