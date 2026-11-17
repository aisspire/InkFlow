import { navigate, useRoute } from './navigation';
import { useEffect, useState } from 'react';
import { Alert, Button, MenuItem, TextField } from '@mui/material';
import { api } from './api';
import type { RunSummary, WorkflowSummary } from './types';
import './workflow-studio.css';

type Source = { kind: 'http' | 'rss' | 'project'; url: string; network: 'public' | 'private'; credential_ref: string; workspace_id: string; files: string[] };
type Task = { id: string; name: string; enabled: boolean; checking: boolean; generation: number; last_check: string | null; next_check: number; last_error: string; failures: number; interval_seconds: number; timezone: string; version_id: string; version_policy: string; source: Source; backlog: number; waiting: number; target_mode: string; document_id: string };
type Change = { id: string; snapshot_id: string; created_at: string; state: string; run_id: string | null; run_status?: string; document_id: string; error: string; version_id: string | null };
type Detail = { task: Task; changes: Change[]; checks: { id: string; at: string; status: string; error: string }[] };
type Doc = { document_id: string; path: string; revision: string };
const states: Record<string, string> = { pending: '等待派发', conflict: '需要重新审阅', blocked: '需要处理', running: '运行中', waiting: '等待人工', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断', verification_required: '等待核验', recovery_blocked: '恢复受阻', recovering: '恢复中', changed: '发现变化', unchanged: '无变化' };
const time = (value: string | number | null, zone = 'Asia/Shanghai') => value === null ? '尚未检查' : new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', { timeZone: zone, hour12: false });

export default function AutomationCenter({ open, onOpenRun, onOpenDocument }: { open: boolean; onOpenRun: (run: RunSummary) => Promise<void>; onOpenDocument: (id: string) => Promise<void> }) {
  const route = useRoute();
  const [tasks, setTasks] = useState<Task[]>([]), [detail, setDetail] = useState<Detail | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]), [docs, setDocs] = useState<Doc[]>([]), [spaces, setSpaces] = useState<{ id: string; root: string }[]>([]);
  const [creating, setCreating] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [loading, setLoading] = useState(true);
  const [name, setName] = useState(''), [workflow, setWorkflow] = useState(''), [documentId, setDocumentId] = useState(''), [mode, setMode] = useState('existing'), [policy, setPolicy] = useState('fixed'), [interval, setIntervalValue] = useState(3600);
  const [source, setSource] = useState<Source>({ kind: 'http', url: '', network: 'public', credential_ref: '', workspace_id: '', files: [] });
  const [evidence, setEvidence] = useState<{ content: string; url: string; fetched_at: string; sha256: string } | null>(null);
  const [retryChange, setRetryChange] = useState<Change | null>(null), [reviewDoc, setReviewDoc] = useState<(Doc & { text: string }) | null>(null);
  async function reload() { setTasks(await api<Task[]>('/automations')); if (detail) { const value = await api<Detail>(`/automations/${detail.task.id}`); setDetail(current => current?.task.id === value.task.id ? value : current); } }
  async function act(work: () => Promise<void>) { setBusy(true); setError(''); try { await work(); await reload(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    void Promise.all([api<Task[]>('/automations'), api<WorkflowSummary[]>('/workflows'), api<Doc[]>('/documents'), api<{ workspaces: { id: string; root: string }[] }>('/workspaces')]).then(([t, w, d, s]) => { setTasks(t); setWorkflows(w); setDocs(d); setSpaces(s.workspaces); }).catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [open]);
  useEffect(() => { if (!open) return; let active = true; const timer = setInterval(() => { void Promise.all([api<Task[]>('/automations'), detail ? api<Detail>(`/automations/${detail.task.id}`) : Promise.resolve(null)]).then(([t, d]) => { if (active) { setTasks(t); if (d) setDetail(d); } }).catch(e => { if (active) setError(e.message); }); }, 2000); return () => { active = false; clearInterval(timer); }; }, [open, detail?.task.id]);
  useEffect(() => { if (!open) return; if (!route.task) { setDetail(null); return; } let live = true; void api<Detail>('/automations/' + route.task).then(d => { if (live) setDetail(d); }).catch(e => setError(e.message)); return () => { live = false; }; }, [open, route.task]);
  const task = tasks.find(t => t.id === detail?.task.id) ?? detail?.task;
  return <section className="workflow-studio automation-center" aria-label="持续关注">
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      <div className="studio-toolbar"><Button variant="contained" onClick={() => setCreating(v => !v)}>{creating ? '收起创建' : '创建持续任务'}</Button><Button disabled={busy} onClick={() => void act(reload)}>刷新任务</Button></div>
      {creating && <form className="automation-form" onSubmit={e => { e.preventDefault(); void act(async () => { const value = await api<Task>('/automations', { method: 'POST', body: JSON.stringify({ name, source, workflow_id: workflow, target_mode: mode, document_id: mode === 'existing' ? documentId : '', version_policy: policy, interval_seconds: interval, timezone: 'Asia/Shanghai', enabled: true }) }); setDetail(await api<Detail>(`/automations/${value.id}`)); navigate({ task: value.id }); setCreating(false); }); }}>
        <TextField label="任务名称" required value={name} onChange={e => setName(e.target.value)} />
        <TextField select label="来源类型" value={source.kind} onChange={e => setSource({ ...source, kind: e.target.value as Source['kind'] })}>{[['http', 'HTTP 页面'], ['rss', 'RSS / Atom'], ['project', '项目文件变化']].map(([v, l]) => <MenuItem key={v} value={v}>{l}</MenuItem>)}</TextField>
        {source.kind === 'project' ? <><TextField select required label="来源工作区" value={source.workspace_id} onChange={e => setSource({ ...source, workspace_id: e.target.value })}>{spaces.map(s => <MenuItem key={s.id} value={s.id}>{s.root}</MenuItem>)}</TextField><TextField label="来源相对文件路径，每行一个" required multiline minRows={2} value={source.files.join('\n')} onChange={e => setSource({ ...source, files: e.target.value.split('\n') })} helperText="Markdown、MDX、TXT、RST；自动排除目标文章和本应用输出。" /></> : <><TextField label="来源 URL" type="url" required value={source.url} onChange={e => setSource({ ...source, url: e.target.value })} /><TextField select label="来源网络" value={source.network} onChange={e => setSource({ ...source, network: e.target.value as Source['network'] })}><MenuItem value="public">公开互联网</MenuItem><MenuItem value="private">明确授权此地址的本机 / 内网读取</MenuItem></TextField><TextField label="凭据引用（可选）" value={source.credential_ref} onChange={e => setSource({ ...source, credential_ref: e.target.value })} helperText="服务器环境变量 INKFLOW_SOURCE_ 开头；不填写密钥正文。" /></>}
        <TextField select label="目标" value={mode} onChange={e => setMode(e.target.value)}><MenuItem value="existing">维护已有文章</MenuItem><MenuItem value="new">每次变化创建未命名候选</MenuItem></TextField>
        {mode === 'existing' && <TextField select required label="目标文章" value={documentId} onChange={e => setDocumentId(e.target.value)}>{docs.map(d => <MenuItem key={d.document_id} value={d.document_id}>{d.path}</MenuItem>)}</TextField>}
        <TextField select required label="处理流程" value={workflow} onChange={e => setWorkflow(e.target.value)}>{workflows.map(w => <MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}</TextField>
        <Button disabled={busy} onClick={() => void act(async () => { const v = await api<{ active: { workflow_id: string } }>('/automations/template', { method: 'POST', body: '{}' }); setWorkflows(await api<WorkflowSummary[]>('/workflows')); setWorkflow(v.active.workflow_id); })}>创建并启用来源审阅模板</Button>
        <TextField select label="流程版本策略" value={policy} onChange={e => setPolicy(e.target.value)}><MenuItem value="fixed">固定创建时的启用版本</MenuItem><MenuItem value="latest">每次派发采用最新已启用版本</MenuItem></TextField>
        <TextField label="检查间隔（秒）" type="number" required value={interval} slotProps={{ htmlInput: { min: 60, max: 2592000 } }} onChange={e => setIntervalValue(Number(e.target.value))} />
        <p className="automation-wide">首次检查也生成候选。时区 Asia/Shanghai；停机错过的检查合并一次。来源变化先审阅，源文件保存与发布分别确认。</p>
        <Button type="submit" variant="contained" disabled={busy || !workflow}>创建并开始关注</Button>
      </form>}
      {loading && <p role="status">正在读取任务…</p>}
      {!loading && !tasks.length && <p>还没有持续任务。创建任务，选择来源、目标文章和处理流程。</p>}
      <div className="automation-layout"><nav aria-label="持续任务" className="automation-list">{tasks.map(t => <Button key={t.id} variant={detail?.task.id === t.id ? 'outlined' : 'text'} onClick={() => void act(async () => { navigate({ task: t.id }); setEvidence(null); })}><strong>{t.name}</strong><span>{!t.enabled ? '已暂停' : t.checking ? '检查中' : t.failures ? '重试等待' : t.waiting ? '等待人工' : '持续关注'} · 积压 {t.backlog} · 待办 {t.waiting}</span></Button>)}</nav>
      {detail && task && <section className="automation-detail" aria-label="任务详情"><h2>{task.name}</h2><p>{task.source.kind === 'project' ? task.source.files.join('、') : task.source.url}</p><p>最近：{time(task.last_check, task.timezone)}<br />下次：{task.enabled ? time(task.next_check, task.timezone) : '暂停期间不触发'} · 每 {task.interval_seconds} 秒 · {task.timezone}</p><p>{task.version_policy === 'fixed' ? `固定版本 ${task.version_id.slice(0, 12)}` : '派发时采用最新已启用版本'} · 补跑合并一次 · 先人工审阅</p>
        {task.last_error && <Alert severity="warning">{task.last_error}</Alert>}
        <div className="button-row"><Button disabled={busy} onClick={() => void act(async () => { await api(`/automations/${task.id}/control`, { method: 'POST', body: JSON.stringify({ generation: task.generation, action: task.enabled ? 'pause' : 'resume' }) }); })}>{task.enabled ? '暂停新触发' : '恢复关注'}</Button><Button disabled={busy || !task.enabled || task.checking || !!task.failures && task.next_check > Date.now() / 1000} onClick={() => void act(async () => { await api(`/automations/${task.id}/control`, { method: 'POST', body: JSON.stringify({ generation: task.generation, action: 'check' }) }); })}>立即检查</Button></div>
        <h3>来源变化与候选</h3>{!detail.changes.length && <p>尚无来源变化。</p>}{detail.changes.map(c => <article key={c.id} className="automation-change"><p>{time(c.created_at, task.timezone)} · {states[c.run_status ?? c.state] ?? c.state}{c.version_id ? ` · 版本 ${c.version_id.slice(0, 12)}` : ''}</p>{c.error && <Alert severity="warning">{c.error}</Alert>}<div className="button-row"><Button disabled={busy} onClick={() => void act(async () => { setEvidence(await api(`/automations/${task.id}/snapshots/${c.snapshot_id}`)); })}>查看来源证据</Button>{c.run_id && <Button disabled={busy} onClick={() => void act(async () => { await onOpenRun(await api<RunSummary>(`/runs/${c.run_id}?document_id=${c.document_id}`)); })}>打开候选与待办</Button>}{c.document_id && !c.run_id && <Button onClick={() => void act(async () => { await onOpenDocument(c.document_id); })}>打开目标文章</Button>}{['conflict', 'blocked'].includes(c.state) && <Button disabled={busy} onClick={() => void act(async () => { setReviewDoc(c.document_id ? await api(`/documents/${c.document_id}`) : null); setRetryChange(c); })}>核对当前稿后重试</Button>}</div></article>)}
        <details><summary>最近 50 次检查</summary>{detail.checks.map(c => <p key={c.id}>{time(c.at, task.timezone)} · {states[c.status]} {c.error}</p>)}</details>
      </section>}</div>
    <section hidden={!evidence} className="evidence-panel" aria-label="来源证据"><h3>来源证据</h3>{evidence && <><p className="automation-break">{evidence.url}<br />{evidence.fetched_at}<br />SHA-256：{evidence.sha256}</p><pre className="automation-evidence">{evidence.content}</pre></>}<Button onClick={() => setEvidence(null)}>收起证据</Button></section>
    <section hidden={!retryChange} className="evidence-panel" aria-label="按当前稿重新审阅"><h3>按当前稿重新审阅</h3><p>保留来源证据，使用下面的文章版本重新产生候选。仍需在文章页确认采用。</p><pre className="automation-evidence">{reviewDoc?.text ?? '创建新文候选'}</pre><div className="button-row"><Button onClick={() => setRetryChange(null)}>取消</Button><Button disabled={busy} onClick={() => void act(async () => { await api(`/automations/${task!.id}/changes/${retryChange!.id}/retry`, { method: 'POST', body: JSON.stringify({ revision: reviewDoc?.revision ?? '' }) }); setRetryChange(null); })}>按此版本重试</Button></div></section>
  </section>;
}
