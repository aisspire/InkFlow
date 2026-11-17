import { useState } from 'react';
import { Alert, Button, TextField } from '@mui/material';

// JSON remains lossless in advanced mode; form edits retain unknown fields.
export default function SampleFields({ value, onChange, label = '固定样例变量' }: { value: string; onChange: (value: string) => void; label?: string }) {
  const [advanced, setAdvanced] = useState(false);
  let fields: Record<string, unknown> | null = null;
  try { const parsed = JSON.parse(value); if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) fields = parsed; } catch { /* show raw input for repair */ }
  return <div className="form-stack"><Button size="small" onClick={() => setAdvanced(v => !v)}>{advanced ? '使用样例表单' : '高级 JSON 编辑'}</Button>
    {advanced || !fields ? <><TextField label={label + '（JSON）'} multiline minRows={4} value={value} onChange={e => onChange(e.target.value)} />{!fields && <Alert severity="warning">请先修复 JSON 对象，再返回表单。</Alert>}</> : Object.entries(fields).map(([key, v]) => <TextField key={key} label={key} multiline minRows={2} value={typeof v === 'string' ? v : JSON.stringify(v)} onChange={e => {
      let next: unknown = e.target.value;
      if (typeof v !== 'string') { try { next = JSON.parse(e.target.value); } catch { return; } }
      onChange(JSON.stringify({ ...fields, [key]: next }, null, 2));
    }} />)}
  </div>;
}
