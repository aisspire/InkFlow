import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, FormControlLabel } from '@mui/material';
import { api } from './api';
import type { Run, WorkflowApplyRequest, WorkflowImport } from './types';
import DocumentDiff from './DocumentDiff';

type Target = { document_id: string; path: string; revision: string; workflow_import?: WorkflowImport };
type Comparison = WorkflowApplyRequest & { conflict: boolean; linked: boolean; candidate: string; label: string };
export default function WorkflowDocumentBridge({ run, target, text, busy, onOpen, onChoose, onApply, onSubmit }: {
  run: Run | null; target: Target | null; text: string; busy: boolean;
  onOpen: (path: string) => void; onChoose: () => void;
  onApply: (value: WorkflowApplyRequest) => Promise<void>; onSubmit: () => Promise<void>;
}) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [confirmed, setConfirmed] = useState(false), [pending, setPending] = useState(false), [error, setError] = useState('');
  const result = run?.document_result;
  const latest = useRef({ runId: run?.id, resultId: result?.id, path: target?.document_id, revision: target?.revision, text });
  latest.current = { runId: run?.id, resultId: result?.id, path: target?.document_id, revision: target?.revision, text };
  useEffect(() => { setComparison(null); setConfirmed(false); setError(''); }, [run?.id, result?.id, target?.document_id]);
  if (!run || !result) return null;
  const associated = run.editor_source;
  const wrongTarget = associated && associated.document_id !== target?.document_id;
  const imported = target?.workflow_import?.run_id === run.id;
  const currentResult = imported && target?.workflow_import?.result_id === result.id;
  async function compare() {
    if (!target || !result || !run) return;
    setPending(true); setError(''); setConfirmed(false);
    const snapshot = { runId: run.id, document_id: target.document_id, path: target.path, revision: target.revision, text, result_id: result.id, confirm_conflict: false };
    try {
      const value = await api<{ conflict: boolean; linked: boolean }>(`/runs/${run.id}/document-preview?document_id=${target.document_id}`, { method: 'POST', body: JSON.stringify({ document_id: target.document_id, revision: target.revision, text, result_id: result.id }) });
      const current = latest.current;
      if (current.runId !== snapshot.runId || current.resultId !== snapshot.result_id || current.path !== snapshot.document_id) return;
      if (current.revision !== snapshot.revision || current.text !== snapshot.text) throw new Error('编辑稿已变化，请重新对比。');
      setComparison({ ...snapshot, ...value, candidate: result.text, label: result.label });
    } catch (err) { setError((err as Error).message); } finally { setPending(false); }
  }
  return <>
    <section className="workflow-document-banner" aria-label="工作流稿件">
      <div><strong>{currentResult ? '候选已采用到工作副本' : `工作流已提供${result.label}`}</strong><small>{associated ? `关联文章：${associated.path}` : '此运行未关联文章，可选择载入位置。'}{imported && !currentResult ? ' · 有新的工作流版本' : ''}</small></div>
      {wrongTarget ? <Button disabled={busy || pending} onClick={() => onOpen(associated.document_id)}>打开关联文章</Button> : !target ? <Button onClick={onChoose}>选择文章</Button> : <Button disabled={busy || pending} onClick={() => void compare()}>{pending ? '正在对比…' : '对比并采用候选'}</Button>}
      {imported && target?.workflow_import?.kind === 'article' && run.pending?.kind === 'final_review' && <Button variant="outlined" disabled={busy || pending} onClick={() => { setPending(true); setError(''); void onSubmit().catch(err => setError((err as Error).message)).finally(() => setPending(false)); }}>提交编辑稿到工作流</Button>}
    </section>
    {error && !comparison && <Alert severity="error" className="editor-alert" onClose={() => setError('')}>{error}</Alert>}
    <section hidden={!comparison} className="candidate-review-panel" aria-label="候选对比"><h2>采用{comparison?.label}到工作副本</h2>
        <p className="workflow-document-help">目标：{comparison?.path}。当前编辑稿将保留到历史；载入后点击“保存”才写回原文件。</p>
        {comparison?.conflict && <Alert severity="warning">{comparison.linked ? '当前编辑稿已不同于工作流输入，请核对差异后确认替换。' : '此运行未关联文章，请确认所选文章可以被这份结果替换。'}</Alert>}
        {error && <Alert severity="error">{error}</Alert>}
        {comparison && <DocumentDiff before={comparison.text} after={comparison.candidate} beforeLabel="当前编辑稿" afterLabel={comparison.label} initialLayout="split" expandable={false} />}
        {comparison?.conflict && <FormControlLabel control={<Checkbox checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />} label="我已核对差异，允许替换当前编辑稿" />}
      <div className="button-row"><Button disabled={pending} onClick={() => { setComparison(null); setError(''); }}>保留当前稿</Button><Button variant="contained" disabled={pending || !!comparison?.conflict && !confirmed} onClick={() => {
        if (!comparison) return;
        setPending(true); setError('');
        void onApply({ runId: comparison.runId, document_id: comparison.document_id, path: comparison.path, revision: comparison.revision, text: comparison.text, result_id: comparison.result_id, confirm_conflict: confirmed }).then(() => setComparison(null)).catch(err => setError((err as Error).message)).finally(() => setPending(false));
      }}>{pending ? '正在载入…' : '采用候选到工作副本'}</Button></div>
    </section>
  </>;
}
