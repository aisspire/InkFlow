"""Isolated P6 provider double for real process and browser verification only."""
from copy import deepcopy
import json
import time
from test_ai import response


def proposal_reply(messages, **kwargs):
    context = json.loads(messages[1]['content'].split('\n已安装节点：')[0])
    if 'slow' in context['goal']:
        time.sleep(30)
    value = json.loads(response(messages))
    if context['diagnostic']:
        d = deepcopy(context['base']['definition'])
        for n in d['nodes']:
            for p in n['prompts'].values():
                p['messages'][0]['content'] += '\nP6 corrected: 保留原输出协议。'
            if n['model']:
                n['model']['temperature'] = .2
        value['definition'] = d
        value['analysis'] = {'category': 'prompt', 'evidence': ['固定失败尝试的输出无法按原协议解析。'], 'uncertainty': '仅替身验证；真实模型结果需要独立评估。'}
    if 'forbidden' in context['goal']:
        value['definition']['capabilities'] = ['local-blog']
    return json.dumps(value, ensure_ascii=False)


def model_reply(messages, **kwargs):
    return '{"suggestions": []}' if 'P6 corrected' in json.dumps(messages) else 'invalid-model-json'


if __name__ == '__main__':
    import os
    from pathlib import Path
    import sys
    from unittest.mock import patch
    import uvicorn
    from inkflow.web.app import create_app
    root, port = Path(sys.argv[1]), int(sys.argv[2])
    os.environ['OPENAI_API_KEY'] = 'p6-isolated-provider-double'
    def observed(messages, **kwargs):
        with (root / 'ai-calls.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'sent': True}) + '\n')
        return proposal_reply(messages, **kwargs)
    with patch('inkflow.ai.service.call_llm', observed), patch('inkflow.llm.core.call_llm', model_reply), patch('inkflow.workflows.full_check.call_llm', model_reply), patch('inkflow.llm.core._create_openai_client', side_effect=AssertionError('Real provider forbidden')):
        uvicorn.run(create_app(root / 'config.toml', root / 'data', root), host='127.0.0.1', port=port, access_log=False, log_level='warning')
