import { useEffect, useState } from 'react';
import { Alert, Button, MenuItem, TextField, Typography } from '@mui/material';
import { api, formatTime } from './api';
import DocumentDiff from './DocumentDiff';
import { openAI } from './navigation';
type Attempt = { id: string; node_id: string; kind: string; status: string; started_at: string; duration_ms: number | null; prompt_revision?: string; error?: { category: string; message: string }; usage?: unknown; truncated: boolean };
const json = (value: unknown) => JSON.stringify(value, null, 2);
export default function NodeDiagnostics({ base, nodeId, documentId, refresh }: { base: string; nodeId?: string; documentId?: string; refresh?: unknown }) {
  const [attempts, setAttempts] = useState<Attempt[]>([]), [error, setError] = useState('');
  const [detail, setDetail] = useState<unknown>(null), [comparison, setComparison] = useState<unknown>(null);
  const headers = documentId ? { 'X-InkFlow-Document': documentId } : undefined;
  useEffect(() => { setDetail(null); setComparison(null); }, [base, nodeId]);
  useEffect(() => {
    let active = true;
    void api<Attempt[]>(`${base}/attempts`, { headers }).then(value => { if (active) { setAttempts(value.filter(a => !nodeId || a.node_id === nodeId)); setError(''); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [base, nodeId, documentId, refresh]);
  async function load(id: string, compare = false) { try { const value = await api(`${base}/attempts/${id}`, { headers }); if (compare) setComparison(value); else setDetail(value); } catch (e) { setError((e as Error).message); } }
  return <section className="form-stack" aria-label="节点诊断"><Typography variant="subtitle2">节点诊断</Typography>{error && <Alert severity="error">{error}</Alert>}{!attempts.length && <Typography variant="caption">暂无结构化诊断；旧运行的提示词版本未知。</Typography>}
    {attempts.map(attempt => <div key={attempt.id}><Button onClick={() => void load(attempt.id)}>{attempt.kind === 'model' ? '模型调用' : '节点执行'} · {attempt.status} · {formatTime(attempt.started_at)} · {attempt.duration_ms === null ? '进行中' : `${attempt.duration_ms} ms`}</Button>{attempt.error && <Typography color="error">{attempt.error.category}：{attempt.error.message}</Typography>}{base.startsWith('/runs/') && ['failed', 'interrupted'].includes(attempt.status) && <Button onClick={() => openAI({ run_id: base.split('/')[2], node_id: attempt.node_id, attempt_id: attempt.id }, documentId)}>AI 分析并提出修复</Button>}</div>)}
    {!!attempts.length && <TextField select label="选择尝试进行比较" value="" onChange={e => void load(e.target.value, true)}><MenuItem value="">选择尝试</MenuItem>{attempts.map(a => <MenuItem key={a.id} value={a.id}>{a.kind} · {a.id.slice(0, 10)} · {a.status}</MenuItem>)}</TextField>}
    {detail !== null && <><Typography variant="caption">完整诊断 · 未截断，凭据已过滤。usage 为 null 表示供应商未返回用量。</Typography><pre className="data-preview">{json(detail)}</pre></>}{comparison !== null && detail !== null && <DocumentDiff before={json(comparison)} after={json(detail)} beforeLabel="比较基准" afterLabel="当前尝试" />}
  </section>;
}
