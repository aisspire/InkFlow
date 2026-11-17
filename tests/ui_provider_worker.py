"""Deterministic UI-only provider; no network or real credentials."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from ai_worker import model_reply, proposal_reply


def ui_reply(messages, **kwargs):
    combined = json.dumps(messages, ensure_ascii=False)
    if 'INKFLOW_UI_RISK' in combined:
        numbered = messages[-1]['content'].split('带行号文本：\n')[-1]
        for line in numbered.splitlines():
            if 'UI_SECRET' in line:
                number = int(line.split(':', 1)[0])
                return json.dumps([{
                    'id': 'S001', 'risk': 'high', 'line_start': number, 'line_end': number,
                    'original_excerpt': 'UI_SECRET', 'issue': '测试敏感片段',
                    'reason': '隔离 UI 验收样例', 'suggestion': '改为公开样例',
                }], ensure_ascii=False)
        return '[]'
    if 'INKFLOW_UI_REWRITE' in combined:
        # The built-in template appends exactly one newline after the variable.
        return messages[-1]['content'].rsplit('\n原文:\n', 1)[1][:-1].replace('UI_SECRET', '公开样例')
    return model_reply(messages, **kwargs)


if __name__ == '__main__':
    import uvicorn
    from inkflow.web.app import create_app

    root, port = Path(sys.argv[1]), int(sys.argv[2])
    os.environ['OPENAI_API_KEY'] = 'isolated-ui-provider-double'
    with patch('inkflow.ai.service.call_llm', proposal_reply), \
         patch('inkflow.llm.core.call_llm', ui_reply), \
         patch('inkflow.workflows.full_check.call_llm', ui_reply), \
         patch('inkflow.services.redaction.call_llm', ui_reply), \
         patch('inkflow.services.article.call_llm', ui_reply), \
         patch('inkflow.llm.core._create_openai_client', side_effect=AssertionError('Real provider forbidden')):
        uvicorn.run(create_app(root / 'config.toml', root / 'data', root),
                    host='127.0.0.1', port=port, access_log=False, log_level='warning')
