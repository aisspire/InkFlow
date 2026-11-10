import { useRef, useState } from 'react';
import { Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Switch, TextField } from '@mui/material';
import { api, requestId } from './api';
import type { Run, WorkflowSummary } from './types';
export default function NewRunDialog({ open, workflow, currentDocument, onClose, onCreated }: {
  open: boolean; workflow: WorkflowSummary; currentDocument?: { path: string; text: string; revision: string };
  onClose: () => void; onCreated: (run: Run) => void;
}) {
  const [name, setName] = useState(''), [noPublish, setNoPublish] = useState(false);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const attempt = useRef<{ body: string; id: string } | null>(null);
  async function create() {
    if (!currentDocument) return;
    setBusy(true); setError('');
    const { path, revision, text } = currentDocument;
    const data = { workflow_id: workflow.id, name, raw_text: text, source_name: path, no_publish: noPublish, editor_source: { path, revision } };
    const body = JSON.stringify(data);
    if (attempt.current?.body !== body) attempt.current = { body, id: requestId() };
    try { onCreated(await api<Run>('/runs', { method: 'POST', body: JSON.stringify({ ...data, request_id: attempt.current.id }) })); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <Dialog open={open} onClose={busy ? undefined : onClose} fullWidth aria-labelledby="new-run-title"><DialogTitle id="new-run-title">启动{workflow.name}</DialogTitle><DialogContent><div className="form-stack">{error && <Alert severity="error">{error}</Alert>}<p>{currentDocument?.path}</p><TextField label="运行名称（可选）" value={name} onChange={e => setName(e.target.value)} /><FormControlLabel control={<Switch checked={noPublish} onChange={e => setNoPublish(e.target.checked)} />} label="本轮禁用博客写入" /></div></DialogContent><DialogActions><Button disabled={busy} onClick={onClose}>取消</Button><Button variant="contained" disabled={busy || !currentDocument} onClick={() => void create()}>基于当前工作副本启动</Button></DialogActions></Dialog>;
}
