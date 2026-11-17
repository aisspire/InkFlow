"""受限、单次变量替换。模板文本与输出协议是资产，解析代码不是用户配置。"""
from copy import deepcopy
import json
from pathlib import Path
import re

BUILTINS = json.loads(Path(__file__).with_name('builtin_prompts.json').read_text(encoding='utf-8'))
TOKEN = re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}')
PROTOCOLS = {
    'full-check': ['"suggestions"', '"kind"', '"original"', '"replacement"', '"occurrence"'],
    'risk-check': ['id:', 'risk:', 'line_start:', 'line_end:', 'original_excerpt:', 'issue:', 'reason:', 'suggestion:'],
    'article-generation': ['"needs_user_input"', '"question"', '"article"', '"title"', '"slug"', '"body"'],
    'redaction-rewrite': ['只返回修改后的完整正文'],
    'legacy-draft': [],
}


class AssetValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__('；'.join(f"{e['field']}: {e['message']}" for e in errors))


def invalid(field, message):
    raise AssetValidationError([{'field': field, 'message': message}])


def validate_prompt(prompt, identity):
    base = BUILTINS[identity]
    if not isinstance(prompt, dict):
        invalid(f'prompts.{identity}', '必须为提示词对象。')
    if set(prompt) != set(base) or prompt.get('id') != identity or prompt.get('contract') != base['contract'] or prompt.get('output_schema') != base['output_schema']:
        invalid(f'prompts.{identity}.contract', '提示词身份、字段与输出协议不可修改；解析器变更需要代码发布。')
    if not isinstance(prompt.get('revision'), str) or not prompt['revision'] or len(prompt['revision']) > 100:
        invalid(f'prompts.{identity}.revision', '修订标识必须为 1–100 字符文本；启用时由服务生成不可变修订。')
    variables = prompt.get('variables')
    if not isinstance(variables, dict) or set(variables) != set(base['variables']):
        invalid(f'prompts.{identity}.variables', '必须保留节点声明的全部变量。')
    for key, spec in variables.items():
        path = f'prompts.{identity}.variables.{key}'
        if not isinstance(spec, dict) or spec.get('type') != base['variables'][key]['type'] or set(spec) - {'type', 'default', 'sample'}:
            invalid(path, '变量类型不可修改，只能设置样例与默认值。')
        for field in ('sample', 'default'):
            if field in spec:
                check_type(spec[field], spec['type'], path + '.' + field)
    messages = prompt.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
        invalid(f'prompts.{identity}.messages', '请提供 1–20 条消息。')
    used = set()
    for i, message in enumerate(messages):
        path = f'prompts.{identity}.messages.{i}'
        if not isinstance(message, dict) or set(message) != {'role', 'content'}:
            invalid(path, '每条消息必须包含 role 与 content。')
        if not isinstance(message['role'], str) or message['role'] not in {'system', 'user', 'assistant'}:
            invalid(path + '.role', '支持 system、user、assistant。')
        content = message['content']
        if not isinstance(content, str) or not content.strip() or len(content) > 100000:
            invalid(path + '.content', '模板必须为非空文本，最多 100000 字符。')
        names = set(TOKEN.findall(content))
        if names - set(variables):
            invalid(path + '.content', '未知变量：' + ', '.join(sorted(names - set(variables))))
        remainder = TOKEN.sub('', content)
        if '{{' in remainder or '}}' in remainder:
            invalid(path + '.content', '变量只能写作 {{变量名}}，不支持表达式、属性或文件读取。JSON 使用普通单花括号。')
        used.update(names)
    if set(variables) - used:
        invalid(f'prompts.{identity}.messages', '模板缺少节点输入：' + ', '.join(sorted(set(variables) - used)))
    combined = '\n'.join(m['content'] for m in messages)
    for required in PROTOCOLS[identity]:
        if required not in combined:
            invalid(f'prompts.{identity}.messages', f'输出协议要求保留 {required}；请恢复协议字段后重试。')


def check_type(value, kind, field):
    expected = {'string': str, 'object': dict}[kind]
    if not isinstance(value, expected):
        invalid(field, '请填写文本。' if kind == 'string' else '请填写 JSON 对象。')


def render(prompt, values):
    validate_prompt(prompt, prompt['id'])
    if not isinstance(values, dict):
        invalid('variables', '请填写变量对象。')
    specs = prompt['variables']
    if set(values) - set(specs):
        invalid('variables', '未知变量：' + ', '.join(sorted(set(values) - set(specs))))
    rendered = {}
    for key, spec in specs.items():
        if key not in values and 'default' not in spec:
            invalid('variables.' + key, '缺少变量，请填写固定样例或声明默认值。')
        value = values.get(key, spec.get('default'))
        check_type(value, spec['type'], 'variables.' + key)
        rendered[key] = json.dumps(value, ensure_ascii=False) if spec['type'] == 'object' else value
    # re.sub 回调返回的数据不会再次解释；正文中的花括号原样保留。
    return [{'role': m['role'], 'content': TOKEN.sub(lambda match: rendered[match[1]], m['content'])} for m in prompt['messages']]


def builtin_messages(identity, values):
    return render(BUILTINS[identity], values)


def initial_prompts(identities):
    return {identity: deepcopy(BUILTINS[identity]) for identity in identities}
