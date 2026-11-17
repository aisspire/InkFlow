import { Alert, Button, MenuItem, TextField, Typography } from '@mui/material';
import ArrowForward from '@mui/icons-material/ArrowForward';
import StopCircleOutlined from '@mui/icons-material/StopCircleOutlined';
import { formatTime } from './api';
import NodeDetails from './NodeDetails';
import { StatusChip } from './Status';
import WorkflowCanvas from './WorkflowCanvas';
import { terminal } from './types';
import type { WorkflowController } from './useWorkflow';

export type AuxiliaryView = 'suggestions' | 'graph' | 'details' | 'runs';
export function WorkflowControls({ controller: c, onStart, onPending }: { controller: WorkflowController; onStart: () => void; onPending: () => void }) {
  const { run } = c;
  const canRecheck = c.workflowId === 'full-check' && run?.workflow_id === 'full-check' && c.active?.id === run?.id && run?.status === 'waiting';
  return <section className="workflow-controls" aria-label="本文运行控制"><strong>本文运行</strong>
    <TextField id="workflow-choice" select size="small" label="下次运行流程" value={c.workflowId} onChange={e => c.setWorkflowId(e.target.value)} disabled={!c.workflows.length}>
      {c.workflows.map(w => <MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
    </TextField>
    <Button variant="outlined" disabled={!c.workflowId || c.busy || !c.hasArticle || !!c.active && !canRecheck || !!run && !terminal(run.status) && !canRecheck} onClick={onStart}>{canRecheck ? '重新检查本文' : '运行本文'}</Button>
    <div className="workflow-run-status" role="status">
      {run ? <><span className="run-name" title={`${run.workflow.name} · ${run.name}`}>{run.workflow.name} · {run.name}</span><StatusChip status={run.status} /></> : <span>未启动 · 可直接编辑文章</span>}
      {c.connection !== 'connected' && <span>{c.connection === 'offline' ? '连接断开' : '连接中'}</span>}
    </div>
    {c.active && c.active.id !== run?.id && <Button onClick={() => c.openRun(c.active!)}>返回当前运行</Button>}
    {run?.pending && <Button className="workflow-pending" color="warning" endIcon={<ArrowForward />} onClick={onPending}>待处理：{run.workflow.nodes.find(n => n.id === run.pending?.node)?.label ?? '人工操作'}</Button>}
    {run && !terminal(run.status) && !['verification_required', 'recovery_blocked'].includes(run.status) && <Button color="error" size="small" startIcon={<StopCircleOutlined />} disabled={c.busy || run.status === 'stopping'} onClick={() => void c.act('stop').catch(err => c.setError((err as Error).message))}>停止</Button>}
    {c.connection === 'offline' && <Button onClick={c.retry}>重连</Button>}
  </section>;
}

export default function WorkflowPanel({ controller: c, view, onView, articleText, onOpenDetails, canSaveProperties }: { controller: WorkflowController; view: AuxiliaryView; onView: (view: AuxiliaryView) => void; articleText: string; canSaveProperties: boolean; onOpenDetails: (run: import('./types').RunSummary) => void }) {
  const { run, workflow } = c;
  return <div className="workflow-panel" hidden={view === 'suggestions'}>
    {run?.workflow_id === 'redaction-only' && run.status === 'completed' && <Alert severity={run.state.review_status === 'ended_with_remaining_risks' ? 'warning' : 'success'}>{run.state.review_status === 'ended_with_remaining_risks' ? '本轮已由人工结束，仍有忽略的风险；未确认无风险。' : '本轮风险检查未发现风险，脱敏处理已结束。'} 源文件仍需显式保存。</Alert>}
    {run?.state.failure && <Alert severity="error">{run.state.failure}</Alert>}
    {run?.status === 'interrupted' && <Alert severity="warning">服务已重启，此运行无法继续。可基于当前工作副本重新运行。</Alert>}
    {run?.state.degraded && <Alert severity="warning">本次生成为降级占位结果，请人工核对内容。</Alert>}
    <div id="auxiliary-panel-graph" role="tabpanel" aria-labelledby="auxiliary-tab-graph" className="workflow-tab-content" hidden={view !== 'graph'}>
      {workflow ? <WorkflowCanvas workflow={workflow} run={run} selected={c.selected} onSelect={id => { c.setSelected(id); onView('details'); }} /> : <p className="editor-empty">正在加载流程…</p>}
    </div>
    <div id="auxiliary-panel-details" role="tabpanel" aria-labelledby="auxiliary-tab-details" className="workflow-tab-content" hidden={view !== 'details'}>
      {workflow && view === 'details' && <NodeDetails workflow={workflow} run={run} selected={c.selected} busy={c.busy} onAction={c.act} onSelect={c.setSelected} articleText={articleText} canSaveProperties={canSaveProperties} />}
    </div>
    <div id="auxiliary-panel-runs" role="tabpanel" aria-labelledby="auxiliary-tab-runs" className="workflow-records" hidden={view !== 'runs'}>
      <h3>本文运行</h3><Typography variant="body2">仅列出当前文章最近 5 次运行。完整详情与时间线在任务页查看。</Typography>
      {!c.history.length && <p>本文尚无运行记录。</p>}
      <div className="workflow-run-list">{c.history.slice(0, 5).map(item => <button key={item.id} onClick={() => onOpenDetails(item)}><strong>{item.name}</strong><small>{item.workflow_name} · {formatTime(item.created_at)}</small><StatusChip status={item.status} /><span>前往完整详情</span></button>)}</div>
      {run && <Button onClick={() => onOpenDetails(run)}>查看当前运行完整详情</Button>}
    </div>
  </div>;
}
