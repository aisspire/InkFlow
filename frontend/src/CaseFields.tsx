import { useState } from 'react';
import { Alert, Button, TextField } from '@mui/material';
type Case = { name: string; text: string; contains: string[]; excludes: string[]; actions: unknown[] };
export default function CaseFields({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [advanced, setAdvanced] = useState(false);
  let cases: Case[] | null = null;
  try { const v = JSON.parse(value); if (Array.isArray(v) && v.every(c => typeof c.name === 'string' && typeof c.text === 'string' && Array.isArray(c.contains) && Array.isArray(c.excludes))) cases = v; } catch { /* advanced mode repairs invalid data */ }
  function update(index: number, patch: Partial<Case>) { onChange(JSON.stringify(cases!.map((c, i) => i === index ? { ...c, ...patch } : c), null, 2)); }
  return <section className="form-stack" aria-label="固定样例与断言"><Button onClick={() => setAdvanced(v => !v)}>{advanced ? '返回样例表单' : '高级 JSON 与模拟动作'}</Button>
    {advanced || !cases ? <><TextField label="固定样例与断言 JSON（最多两组）" multiline minRows={5} value={value} onChange={e => onChange(e.target.value)} />{!cases && <Alert severity="warning">请修复样例格式后返回表单。</Alert>}</> : <>{cases.map((c, i) => <fieldset className="form-stack" key={i}><legend>样例 {i + 1}</legend><TextField label={`样例 ${i + 1} 名称`} value={c.name} onChange={e => update(i, { name: e.target.value })} /><TextField label={`样例 ${i + 1} 正文`} multiline minRows={3} value={c.text} onChange={e => update(i, { text: e.target.value })} />{(['contains', 'excludes'] as const).map(key => <TextField key={key} label={`${key === 'contains' ? '必须包含' : '必须排除'}（每行一条）`} multiline minRows={2} value={c[key].join('\n')} onChange={e => update(i, { [key]: e.target.value.split('\n') })} />)}{cases!.length > 1 && <Button onClick={() => onChange(JSON.stringify(cases!.filter((_, n) => n !== i), null, 2))}>删除此样例</Button>}</fieldset>)}<Button disabled={cases.length >= 2} onClick={() => onChange(JSON.stringify([...cases!, { name: '固定样例 2', text: '', contains: [], excludes: [], actions: [] }], null, 2))}>添加样例</Button></>}
  </section>;
}
