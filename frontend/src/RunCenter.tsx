import { navigate, useRoute } from './navigation';
import { useEffect, useState } from 'react';
import { Alert, Button, MenuItem, TextField } from '@mui/material';
import { api, formatTime, requestId } from './api';
import type { Run, RunSummary, WorkflowSummary } from './types';
import WorkflowCanvas from './WorkflowCanvas';
import NodeDetails from './NodeDetails';
import { StatusChip } from './Status';
import { terminal } from './types';

export default function RunCenter({ open, onOpenArticle }: { open: boolean; onOpenArticle: (run: RunSummary) => Promise<void> }) {
  const route = useRoute();
  const legacy = route.recordScope === 'legacy';
  const selectedRequest = route.run + ":" + route.runDocument;
  const [rows, setRows] = useState<RunSummary[]>([]), [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [documents, setDocuments] = useState<{ document_id: string; path: string }[]>([]), [workspaces, setWorkspaces] = useState<{ id: string; root: string }[]>([]);
  const [status, setStatus] = useState(''), [workflow, setWorkflow] = useState(''), [document, setDocument] = useState(''), [workspace, setWorkspace] = useState('');
  const [run, setRun] = useState<Run | null>(null), [selected, setSelected] = useState<string | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const query = new URLSearchParams({ status, workflow_id: workflow, document_id: document, workspace_id: workspace }).toString();
  const listUrl = legacy ? '/runs?unlinked=true' : '/run-center?' + query;
  async function reload() { setRows(await api(listUrl)); }
  async function detail(id: string, doc: string) { const value = await api<Run>(`/runs/${id}?document_id=${doc}`); if (new URLSearchParams(window.location.search).get('run') === id) setRun(value); return value; }
  useEffect(() => { if (open) void Promise.all([api<WorkflowSummary[]>('/workflows?include_drafts=true'), api<{ document_id: string; path: string }[]>('/documents'), api<{ workspaces: { id: string; root: string }[] }>('/workspaces')]).then(([w, d, s]) => { setWorkflows(w); setDocuments(d); setWorkspaces(s.workspaces); }).catch(e => setError(e.message)); }, [open]);
  useEffect(() => { if (open) void reload().catch(e => setError(e.message)); }, [open, query, legacy]);
  useEffect(() => {
    if (!open || !run || !['running', 'waiting', 'stopping', 'recovering', 'verification_required', 'recovery_blocked'].includes(run.status)) return;
    const timer = setInterval(() => { void detail(run.id, run.editor_source?.document_id ?? '').catch(e => setError(e.message)); }, 900);
    return () => clearInterval(timer);
  }, [open, run?.id, run?.status]);
  useEffect(() => { if (!open || !run?.pending) return; setSelected(run.pending.node); }, [open, run?.pending?.id]);
  useEffect(() => {
    if (!open) return;
    if (!route.run) { setRun(null); return; }
    if (run?.id === route.run && (run.editor_source?.document_id ?? '') === route.runDocument) {
      void detail(route.run, route.runDocument).catch(e => setError(e.message));
      return;
    }
    setRun(null);
    void detail(route.run, route.runDocument).then(value => { if (new URLSearchParams(window.location.search).get('run') === value.id) setSelected(value.pending?.node ?? value.current_node); }).catch(e => setError(e.message));
  }, [open, selectedRequest]);
  async function act(action: string, payload: Record<string, unknown> = {}) {
    if (!run) return;
    setBusy(true); setError('');
    try { await api('/runs/' + run.id + '/actions?document_id=' + (run.editor_source?.document_id ?? ''), { method: 'POST', body: JSON.stringify({ request_id: requestId(), pending_id: run.pending?.id, action, payload }) }); await detail(run.id, run.editor_source?.document_id ?? ''); await reload(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="workflow-studio" aria-label="运行记录">
    {error && <Alert severity="error">{error}</Alert>}
    <div className="studio-toolbar"><TextField select label="记录范围" value={route.recordScope} onChange={e => navigate({ recordScope: e.target.value, run: '', runDocument: '' })}><MenuItem value="documents">已登记文章运行</MenuItem><MenuItem value="legacy">旧版未关联记录</MenuItem></TextField>
      <TextField hidden={legacy} select label="状态" value={status} onChange={e => setStatus(e.target.value)}><MenuItem value="">全部状态</MenuItem>{['recovering', 'verification_required', 'recovery_blocked', 'running', 'waiting', 'completed', 'failed', 'stopped', 'interrupted'].map(v => <MenuItem key={v} value={v}>{({ recovering: '恢复中', verification_required: '等待核验', recovery_blocked: '恢复受阻', running: '运行中', waiting: '待处理', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断' } as Record<string, string>)[v]}</MenuItem>)}</TextField>
      <TextField hidden={legacy} select label="流程" value={workflow} onChange={e => setWorkflow(e.target.value)}><MenuItem value="">全部流程</MenuItem>{workflows.map(w => <MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}</TextField>
      <TextField hidden={legacy} select label="文档" value={document} onChange={e => setDocument(e.target.value)}><MenuItem value="">全部已登记文档</MenuItem>{documents.map(d => <MenuItem key={d.document_id} value={d.document_id}>{d.path}</MenuItem>)}</TextField>
      <TextField hidden={legacy} select label="工作区范围" value={workspace} onChange={e => setWorkspace(e.target.value)}><MenuItem value="">全部已登记范围</MenuItem>{workspaces.map(w => <MenuItem key={w.id} value={w.id}>{w.root}</MenuItem>)}</TextField>
      <Button onClick={() => void reload().catch(e => setError(e.message))}>刷新记录</Button>
    </div>
    {!rows.length && <p>当前筛选没有运行。打开文章后选择工作流即可开始。</p>}
    <div className="run-center-list">{rows.map(row => <Button key={row.id} variant={run?.id === row.id ? 'outlined' : 'text'} onClick={() => navigate({ run: row.id, runDocument: row.editor_source?.document_id ?? '' })}><span className="run-record-label">{row.name} · {row.workflow_name} · {formatTime(row.created_at)}</span><StatusChip status={row.status} /></Button>)}</div>
    {rows.length >= 50 && <Button onClick={() => void api<RunSummary[]>(`${listUrl}&offset=${rows.length}`).then(more => setRows([...rows, ...more])).catch(e => setError(e.message))}>加载更多</Button>}
    {run && <><div className="button-row">{!run.pending && run.editor_source && <Button onClick={() => void onOpenArticle(run).catch(e => setError(e.message))}>打开关联文章</Button>}<StatusChip status={run.status} />{!terminal(run.status) && !['verification_required', 'recovery_blocked'].includes(run.status) && <Button color="error" disabled={busy || run.status === 'stopping'} onClick={() => void act('stop')}>停止此运行</Button>}<span>固定版本 {run.workflow_version?.slice(0, 12) ?? '未知'}</span></div><div className="studio-layout"><div className="studio-graph"><WorkflowCanvas workflow={run.workflow} run={run} selected={selected} onSelect={setSelected} /></div><NodeDetails workflow={run.workflow} run={run} selected={selected} onSelect={setSelected} busy={busy} mode="task" onReview={run.editor_source ? () => void onOpenArticle(run).catch(e => setError(e.message)) : undefined} onAction={act} /></div><section aria-label="运行时间线"><h2>运行时间线</h2>{run.events.map(event => <p key={event.seq}><small>{formatTime(event.time)} · #{event.seq}</small> {event.summary}</p>)}</section></>}
  </section>;
}
