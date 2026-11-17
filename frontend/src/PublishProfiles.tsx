import { useEffect, useState } from 'react';
import { Alert, Button, MenuItem, TextField, Typography } from '@mui/material';
import { api } from './api';
import type { Profile } from './DefinitionEditor';

const defaults = { name: '', repo_path: '', content_dir: 'src/content/blog', capability: 'local-blog', format: 'astro-mdx', naming: 'slug/index.mdx', overwrite: 'confirm' };
export default function PublishProfiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]), [selected, setSelected] = useState('');
  const [form, setForm] = useState<Record<string, string>>({ ...defaults }), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const current = profiles.find(p => p.profile_id === selected);
  const load = () => api<Profile[]>('/publish-profiles').then(setProfiles);
  useEffect(() => { void load().catch(e => setError(e.message)); }, []);
  return <section className="settings-section form-stack" aria-label="共享发布配置"><Typography variant="h6">共享发布配置</Typography><Typography variant="body2">流程节点固定选择某一修订。保存新修订后，请在流程设计器显式选择、保存并启用；已有流程不会随之更新。</Typography>
    {error && <Alert severity="error">{error}</Alert>}{notice && <Alert severity="success">{notice}</Alert>}
    <TextField select label="发布配置" value={selected} onChange={e => { setSelected(e.target.value); setForm({ ...(profiles.find(p => p.profile_id === e.target.value)?.payload ?? defaults) }); setNotice(''); }}><MenuItem value="">创建新配置</MenuItem>{profiles.map(p => <MenuItem key={p.profile_id} value={p.profile_id}>{p.payload.name} · {p.revision.slice(0, 8)}</MenuItem>)}</TextField>
    {(['name', 'repo_path', 'content_dir'] as const).map(key => <TextField key={key} disabled={busy} label={({ name: '配置名称', repo_path: '博客仓库目录', content_dir: '仓库内内容目录' })[key]} value={form[key] ?? ''} onChange={e => setForm({ ...form, [key]: e.target.value })} />)}
    <TextField select disabled={busy} label="覆盖策略" value={form.overwrite} onChange={e => setForm({ ...form, overwrite: e.target.value })}><MenuItem value="confirm">每次明确确认</MenuItem><MenuItem value="never">拒绝覆盖</MenuItem></TextField>
    {current && <details><summary>固定修订与关联流程</summary><p>当前修订 {current.revision}</p>{current.users.map((u, i) => <p key={i}>{u.workflow_id} / {u.node_id} · {u.revision.slice(0, 12)}</p>)}{!current.users.length && <p>尚无关联流程</p>}</details>}
    <Button variant="outlined" disabled={busy || !form.name?.trim()} onClick={() => { setBusy(true); setError(''); setNotice(''); void api<Profile>('/publish-profiles' + (current ? '/' + current.profile_id : ''), { method: current ? 'PUT' : 'POST', body: JSON.stringify({ payload: form, generation: current?.generation ?? 0 }) }).then(async p => { await load(); window.dispatchEvent(new Event('inkflow-profiles-changed')); setSelected(p.profile_id); setForm({ ...p.payload }); setNotice('共享配置修订已保存。已有流程继续使用原固定修订。'); }).catch(e => setError(e.message)).finally(() => setBusy(false)); }}>保存共享配置修订</Button>
  </section>;
}
