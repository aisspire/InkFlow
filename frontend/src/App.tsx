import { Alert, Button, MenuItem, TextField, Typography } from '@mui/material';
import ArrowForward from '@mui/icons-material/ArrowForward';
import Refresh from '@mui/icons-material/Refresh';
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
  return <section className="workflow-controls" aria-label="工作流控制">
    <TextField id="workflow-choice" select size="small" label="工作流" value={c.workflowId} onChange={e => c.setWorkflowId(e.target.value)} disabled={!c.workflows.length}>
      {c.workflows.map(w => <MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}
    </TextField>
    <Button variant="outlined" disabled={!c.workflowId || c.busy || !c.hasArticle || !!c.active || !!run && !terminal(run.status)} onClick={onStart}>启动工作流</Button>
    <div className="workflow-run-status" role="status">
      {run ? <><span className="run-name" title={`${run.workflow.name} · ${run.name}`}>{run.workflow.name} · {run.name}</span><StatusChip status={run.status} /></> : <span>未启动 · 可直接编辑文章</span>}
      {c.connection !== 'connected' && <span>{c.connection === 'offline' ? '连接断开' : '连接中'}</span>}
    </div>
    {c.active && c.active.id !== run?.id && <Button onClick={() => c.openRun(c.active!)}>返回当前运行</Button>}
    {run?.pending && <Button className="workflow-pending" color="warning" endIcon={<ArrowForward />} onClick={onPending}>待处理：{run.workflow.nodes.find(n => n.id === run.pending?.node)?.label ?? '人工操作'}</Button>}
    {run?.pending?.kind === 'suggestion_review' && <Button disabled={c.busy} onClick={() => void c.act('finish_check').catch(err => c.setError(err.message))}>结束本轮</Button>}
    {run && (terminal(run.status) ? <Button size="small" startIcon={<Refresh />} disabled={c.busy || !!c.active || !c.hasArticle} onClick={onStart}>以当前稿重新运行</Button> : <Button color="error" size="small" startIcon={<StopCircleOutlined />} disabled={c.busy || run.status === 'stopping'} onClick={() => void c.act('stop').catch(err => c.setError((err as Error).message))}>停止</Button>)}
    {c.connection === 'offline' && <Button onClick={c.retry}>重连</Button>}
  </section>;
}

export default function WorkflowPanel({ controller: c, view, onView }: { controller: WorkflowController; view: AuxiliaryView; onView: (view: AuxiliaryView) => void }) {
  const { run, workflow } = c;
  return <div className="workflow-panel" hidden={view === 'suggestions'}>
    {run?.state.failure && <Alert severity="error">{run.state.failure}</Alert>}
    {run?.status === 'interrupted' && <Alert severity="warning">服务已重启，此运行无法继续。可基于当前工作副本重新运行。</Alert>}
    {run?.state.degraded && <Alert severity="warning">本次生成为降级占位结果，请人工核对内容。</Alert>}
    <div id="auxiliary-panel-graph" role="tabpanel" aria-labelledby="auxiliary-tab-graph" className="workflow-tab-content" hidden={view !== 'graph'}>
      {workflow ? <WorkflowCanvas workflow={workflow} run={run} selected={c.selected} onSelect={id => { c.setSelected(id); onView('details'); }} /> : <p className="editor-empty">正在加载流程…</p>}
    </div>
    <div id="auxiliary-panel-details" role="tabpanel" aria-labelledby="auxiliary-tab-details" className="workflow-tab-content" hidden={view !== 'details'}>
      {workflow && view === 'details' && <NodeDetails workflow={workflow} run={run} selected={c.selected} busy={c.busy} onAction={c.act} onSelect={c.setSelected} />}
    </div>
    <div id="auxiliary-panel-runs" role="tabpanel" aria-labelledby="auxiliary-tab-runs" className="workflow-records" hidden={view !== 'runs'}>
      <h3>运行记录</h3><Button size="small" onClick={() => c.setLegacy(!c.legacy)}>{c.legacy ? "返回当前文章历史" : "查看旧版未关联历史"}</Button>
      {!c.history.length && <p>启动工作流后，运行记录会出现在这里。</p>}
      <div className="workflow-run-list">{c.history.map(item => <button key={item.id} aria-pressed={run?.id === item.id} onClick={() => { c.openRun(item); onView('graph'); }}><strong>{item.name}</strong><small>{item.workflow_name} · {formatTime(item.created_at)}</small><StatusChip status={run?.id === item.id ? run.status : item.status} /></button>)}</div>
      {c.historyMore && <Button onClick={() => void c.more()}>加载更多记录</Button>}
      <h3>当前运行时间线</h3>
      {run?.events.length ? [...run.events].reverse().map(event => <div className="workflow-event" key={event.seq}><small>#{event.seq} · {formatTime(event.time)}</small><span>{event.summary}</span></div>) : <Typography variant="body2" color="text.secondary">尚无执行记录</Typography>}
      {run && run.event_cursor > 200 && <p>显示最近 200 条事件，完整记录保存在本地数据库。</p>}
    </div>
  </div>;
}
