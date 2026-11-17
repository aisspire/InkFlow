import SampleFields from './SampleFields';
import { navigate, useRoute } from './navigation';
import { useEffect, useRef, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Button, Chip, Divider, MenuItem, TextField, Typography } from '@mui/material';
import ExpandMore from '@mui/icons-material/ExpandMore';
import { api, formatTime } from './api';
import WorkflowCanvas from './WorkflowCanvas';
import DocumentDiff from './DocumentDiff';
import NodeDiagnostics from './NodeDiagnostics';
import AIAssistant from './AIAssistant';
import DefinitionEditor, { definitionGraph, type Definition } from './DefinitionEditor';
import type { Workflow, WorkflowSummary } from './types';
import './workflow-studio.css';

export type PromptAsset = { id: string; revision: string; contract: string; output_schema: unknown; messages: { role: string; content: string }[]; variables: Record<string, { type: string; default?: unknown; sample: unknown }> };
export type VersionPackage = { schema_version?: number; definition?: Definition; compiled?: unknown; workflow_id: string; graph: Workflow; nodes: Record<string, { implementation: string; prompt_id?: string }>; prompts: Record<string, PromptAsset>; model: Record<string, unknown>; node_config: Record<string, Record<string, Record<string, string>>> };
type Version = { id: string; payload: VersionPackage; created_at: string; generation: number };
type Catalog = { active: Version; draft: { revision: number; payload: VersionPackage; base_version: string } | null; versions: { id: string; created_at: string }[]; shared_prompt_users: Record<string, string[]>; protocols: Record<string, string[]> };
type Session = { catalog: Catalog; package: VersionPackage; saved: string; revision: number | null; historyId: string | null; samples: Record<string, string>; selected: string; localBackup?: string };
type TestRecord = { id: string; node_id: string; status: string; created_at: string; prompt_revision: string; result?: unknown; error?: unknown; messages?: unknown; model?: unknown; variables?: unknown; source?: unknown };
export type StudioTarget = { workflowId: string; nodeId?: string; versionId?: string; package?: VersionPackage };
const json = (value: unknown) => JSON.stringify(value, null, 2);
const clone = <T,>(value: T): T => structuredClone(value);
const testLabels: Record<string, string> = { running: '调用中', stopping: '正在停止', stopped: '已停止', failed: '失败', completed: '已完成', interrupted: '已中断' };

export default function WorkflowStudio({ open, target }: { open: boolean; target?: StudioTarget }) {
  const route = useRoute();
  const stage = route.stage;
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [workflowId, setWorkflowId] = useState('full-check');
  const [newName, setNewName] = useState('发布已有文章');
  const sessions = useRef(new Map<string, Session>());
  const [session, setSession] = useState<Session | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [preview, setPreview] = useState<unknown>(null), [previewBody, setPreviewBody] = useState('');
  const [tests, setTests] = useState<TestRecord[]>([]), [test, setTest] = useState<TestRecord | null>(null), [compareTest, setCompareTest] = useState<TestRecord | null>(null);
  const [revisions, setRevisions] = useState<PromptAsset[]>([]), [latest, setLatest] = useState<Catalog | null>(null);
  const currentId = useRef(workflowId);
  const dirty = !!session && json(session.package) !== session.saved;
  const editable = session?.revision !== null && !session?.historyId;
  const node = session?.package.nodes[session.selected];
  const prompt = node?.prompt_id ? session?.package.prompts[node.prompt_id] : undefined;
  function update(value: Session) { sessions.current.set(workflowId, value); setSession(value); setPreviewBody(''); }
  function adopt(catalog: Catalog, id: string) {
    const existing = sessions.current.get(id);
    const value: Session = { catalog, package: clone(catalog.draft?.payload ?? catalog.active.payload), saved: json(catalog.draft?.payload ?? catalog.active.payload), revision: catalog.draft?.revision ?? null, historyId: null, samples: existing?.samples ?? {}, selected: existing?.selected ?? catalog.active.payload.graph.nodes[0]?.id ?? '' };
    sessions.current.set(id, value);
    if (currentId.current === id) setSession(value);
  }
  async function load(id: string) {
    if (currentId.current !== id) { setPreview(null); setTest(null); setCompareTest(null); setLatest(null); setRevisions([]); }
    currentId.current = id; setWorkflowId(id); setError(''); setNotice('');
    const cached = sessions.current.get(id);
    setSession(cached ?? null);
    if (!cached) {
      try { adopt(await api<Catalog>(`/workflow-assets/${id}`), id); } catch (e) { setError((e as Error).message); }
    }
    try { const records = await api<TestRecord[]>(`/workflow-assets/${id}/tests`); if (currentId.current === id) setTests(records); } catch (e) { setError((e as Error).message); }
  }
  useEffect(() => {
    if (!open) return;
    void api<WorkflowSummary[]>('/workflows?include_drafts=true').then(setWorkflows).catch(e => setError(e.message));
    void load(route.workflow).then(async () => {
      if (!target || target.workflowId !== route.workflow || currentId.current !== target.workflowId) return;
      const cached = sessions.current.get(target.workflowId);
      if (!cached) return;
      // Preserve unsaved edits before opening a historical snapshot.
      if (json(cached.package) !== cached.saved) { setNotice('此流程有未保存内容，已保留在编辑区；请先保存，再打开历史版本。'); return; }
      if (target.package) {
        const value = { ...cached, package: clone(target.package), historyId: target.versionId ?? 'legacy', revision: null, saved: json(target.package), selected: target.nodeId ?? cached.selected };
        sessions.current.set(target.workflowId, value); setSession(value);
      } else if (target.nodeId) { const value = { ...cached, selected: target.nodeId }; sessions.current.set(target.workflowId, value); setSession(value); }
    });
  // Opening the studio is navigation; article components remain mounted underneath.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, target, route.workflow]);
  useEffect(() => {
    const protect = (e: BeforeUnloadEvent) => { if ([...sessions.current.values()].some(s => json(s.package) !== s.saved)) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', protect); return () => window.removeEventListener('beforeunload', protect);
  }, []);
  useEffect(() => {
    if (!open || !test || !['running', 'stopping'].includes(test.status)) return;
    const id = workflowId;
    const timer = window.setInterval(() => { void api<TestRecord>(`/workflow-assets/${id}/tests/${test.id}`).then(value => { if (currentId.current === id) { setTest(value); void api<TestRecord[]>(`/workflow-assets/${id}/tests`).then(setTests); } }).catch(e => setError(e.message)); }, 900);
    return () => clearInterval(timer);
  }, [open, workflowId, test]);
  async function action(task: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await task(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  function mutate(change: (p: VersionPackage) => void) { window.dispatchEvent(new CustomEvent('inkflow-definition-edited', { detail: { workflowId } })); const current = sessions.current.get(workflowId); if (!current) return; const p = clone(current.package); change(p); if (p.definition) p.graph = definitionGraph(p); update({ ...current, package: p }); }
  async function createWorkflow(copy = false) {
    const c = await api<Catalog>('/workflow-definitions', { method: 'POST', body: json({ name: newName, source_id: copy ? workflowId : null, version_id: copy ? session?.historyId ?? null : null }) });
    const id = c.draft!.payload.workflow_id; currentId.current = id; setWorkflowId(id); adopt(c, id);
    navigate({ workflow: id, stage: 'design', ai: '' });
    setWorkflows(await api('/workflows?include_drafts=true')); setNotice('独立流程草稿已创建；校验并显式启用后可运行。');
  }
  function sample() { if (!session || !prompt) return '{}'; return session.samples[session.selected] ?? json(Object.fromEntries(Object.entries(prompt.variables).map(([key, spec]) => [key, spec.sample]))); }
  function body() { if (!session) throw new Error('请先选择流程。'); let variables; try { variables = JSON.parse(sample()); } catch { throw new Error('固定样例：JSON 格式错误，请检查引号、逗号与字段类型。'); } return { node_id: session.selected, package: session.package, variables, source: { base_version: session.historyId ?? session.catalog.draft?.base_version ?? session.catalog.active.id, draft_revision: session.revision, saved: !dirty } }; }
  async function history(id: string) {
    if (!session) return;
    if (dirty) { setError('请先保存当前草稿；未保存内容仍保留。'); return; }
    const value = await api<Version>(`/workflow-assets/${workflowId}/versions/${id}`);
    update({ ...session, package: value.payload, saved: json(value.payload), revision: null, historyId: id });
  }
  const status = error ? '校验或操作失败' : dirty ? '草稿未保存' : session?.historyId ? '历史版本 · 只读' : session?.revision ? '草稿已保存 · 尚未启用' : '已启用';
  return <section className="workflow-studio" aria-label="工作流页面">
    <h1 data-page-heading tabIndex={-1}>工作流</h1>
    <div hidden={!!route.ai}>
      <div className="page-tabs" role="group" aria-label="流程视图">{[['design', '设计'], ['test', '测试'], ['versions', '版本']].map(([id, label]) => <Button key={id} aria-pressed={stage === id} onClick={() => navigate({ stage: id })}>{label}</Button>)}</div>
    </div>
    <AIAssistant isDirty={id => { const s = sessions.current.get(id); return !!s && json(s.package) !== s.saved; }} active={open} onOpenWorkflow={id => { navigate({ workflow: id, ai: '', stage: 'design' }); void load(id).then(async () => { const fresh = await api<Catalog>(`/workflow-assets/` + id); const local = sessions.current.get(id); if (local && json(local.package) !== local.saved) { setLatest(fresh); setNotice('本地未保存内容已保留，请核对 AI 草稿差异。'); } else adopt(fresh, id); }).catch(e => setError(e.message)); void api<WorkflowSummary[]>('/workflows?include_drafts=true').then(setWorkflows); }} />
    <div hidden={!!route.ai}>
      <div className="studio-toolbar">

        <details className="workflow-management"><summary>创建与复制</summary><div className="form-stack"><TextField label="新流程名称" value={newName} onChange={e => setNewName(e.target.value)} disabled={busy} />
        <Button disabled={busy} onClick={() => void action(() => createWorkflow())}>创建发布已有文章流程</Button>
        <Button disabled={busy || !session || dirty} onClick={() => void action(() => createWorkflow(true))}>复制所选版本为独立流程</Button></div></details>
        <TextField select label="选择流程资产" value={workflowId} disabled={busy} onChange={e => navigate({ workflow: e.target.value })}>{workflows.map(w => <MenuItem key={w.id} value={w.id}>{w.name}</MenuItem>)}</TextField>
        <Chip label={status} color={error ? 'error' : dirty ? 'warning' : 'default'} role="status" />
        <Button disabled={busy || !!session?.revision} onClick={() => void action(async () => { adopt(await api<Catalog>(`/workflow-assets/${workflowId}/draft`, { method: 'POST', body: json({ version_id: session?.historyId ?? null }) }), workflowId); })}>从此版本创建草稿</Button>
        <Button disabled={busy || !editable} onClick={() => void action(async () => { adopt(await api<Catalog>(`/workflow-assets/${workflowId}/draft`, { method: 'PUT', body: json({ revision: session?.revision, package: session?.package }) }), workflowId); setNotice('流程草稿已保存。新运行仍使用已启用版本。'); })}>保存流程草稿</Button>
        <Button hidden={stage !== 'versions'} variant="contained" disabled={busy || !editable || dirty} onClick={() => void action(async () => { adopt(await api<Catalog>(`/workflow-assets/${workflowId}/activate`, { method: 'POST', body: json({ revision: session?.revision, generation: session?.catalog.active.generation }) }), workflowId); setNotice('新版本已启用，仅影响之后创建的运行。'); window.dispatchEvent(new Event('inkflow-workflows-changed')); setWorkflows(await api('/workflows?include_drafts=true')); })}>启用已保存版本</Button>
        <Button hidden={stage !== 'versions'} disabled={busy} onClick={() => void action(async () => { const value = await api<Catalog>(`/workflow-assets/${workflowId}`); if (dirty) setLatest(value); else adopt(value, workflowId); })}>加载最新草稿比较</Button>
      </div>
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}{notice && <Alert severity="info">{notice}</Alert>}
      {!session ? <p role="status">正在加载流程资产…</p> : <>
        <Typography variant="caption">启用版本 {session.catalog.active.id.slice(0, 12)} · 草稿 revision {session.revision ?? '—'}。保存草稿、启用版本与模型试运行分别执行。</Typography>
        {latest && <section><Typography>服务端最新内容与保留的本地草稿</Typography><DocumentDiff before={json(latest.draft?.payload ?? latest.active.payload)} after={json(session.package)} beforeLabel="服务端" afterLabel="本地未保存内容" /><Button disabled={!latest.draft} onClick={() => { update({ ...session, catalog: latest, revision: latest.draft!.revision, saved: json(latest.draft!.payload) }); setLatest(null); setNotice('本地内容保留，已采用最新 revision；核对差异后可显式保存合并结果。'); }}>已核对，保留本地内容并采用最新 revision</Button></section>}
        <div className={`studio-layout stage-${stage}`} hidden={stage === 'versions'}>
          <div className="studio-graph" hidden={stage !== 'design'}><WorkflowCanvas workflow={session.package.graph} run={null} selected={session.selected} onSelect={selected => { if (busy) return; update({ ...session, selected }); setPreview(null); setRevisions([]); }} /></div>
          {session.package.definition ? <DefinitionEditor view={stage === 'test' ? 'test' : 'design'} value={session.package} selected={session.selected} editable={!!editable} busy={busy} onChange={mutate} onSelect={selected => { const current = sessions.current.get(workflowId); if (current) update({ ...current, selected }); }} /> : <section className="studio-editor form-stack" aria-label="节点资产编辑">
            <TextField select label="选择节点（键盘可操作）" value={session.selected} onChange={e => update({ ...session, selected: e.target.value })}>{session.package.graph.nodes.map(n => <MenuItem key={n.id} value={n.id}>{n.label}</MenuItem>)}</TextField>
            <Typography variant="h6">{session.package.graph.nodes.find(n => n.id === session.selected)?.label}</Typography>
            <details><summary>节点技术详情</summary><Typography variant="caption" className="studio-identifier">{node?.implementation}</Typography></details>
            {prompt ? <>
              <Typography variant="subtitle2">提示词 {prompt.id} · {editable ? '基础修订 ' : '修订 '}{prompt.revision.slice(0, 12)}</Typography>
              <Typography variant="caption">共享于 {session.catalog.shared_prompt_users[prompt.id]?.join('、')}。本次启用仅改变当前流程；其它流程需显式采用修订。</Typography>
              <div hidden={stage !== 'design'} className="form-stack">{prompt.messages.map((message, i) => <div className="form-stack" key={`${prompt.id}-${i}`}>
                <TextField select label={`消息 ${i + 1} 角色`} disabled={!editable || busy} value={message.role} onChange={e => mutate(p => { p.prompts[prompt.id].messages[i].role = e.target.value; })}>{['system', 'user', 'assistant'].map(role => <MenuItem key={role} value={role}>{role}</MenuItem>)}</TextField>
                <TextField label={`消息 ${i + 1} 模板`} multiline minRows={message.role === 'system' ? 3 : 8} maxRows={22} value={message.content} slotProps={{ input: { readOnly: !editable || busy } }} onChange={e => mutate(p => { p.prompts[prompt.id].messages[i].content = e.target.value; })} />
              </div>)}
              <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>变量声明、默认值与不可修改的输出协议</AccordionSummary><AccordionDetails><pre>{json(prompt.variables)}</pre><Typography>{prompt.contract}</Typography><pre>{json(prompt.output_schema)}</pre><Typography variant="caption">模板必须保留这些协议字段，编辑提示词不会更换解析器：</Typography><pre>{json(session.catalog.protocols[prompt.id])}</pre></AccordionDetails></Accordion>
              <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>模型参数 · 此流程全部模型节点</AccordionSummary><AccordionDetails><div className="form-stack">{Object.entries(session.package.model).map(([key, value]) => typeof value === 'boolean' ? <TextField key={key} select label={key} value={String(value)} disabled={!editable || busy} onChange={e => mutate(p => { p.model[key] = e.target.value === 'true'; })}><MenuItem value="true">启用</MenuItem><MenuItem value="false">关闭</MenuItem></TextField> : <TextField key={key} label={key} value={value ?? ''} disabled={!editable || busy} type={typeof value === 'number' || key === 'max_tokens' ? 'number' : 'text'} onChange={e => mutate(p => { p.model[key] = typeof value === 'number' || key === 'max_tokens' ? (e.target.value === '' ? null : Number(e.target.value)) : e.target.value || null; })} />)}</div></AccordionDetails></Accordion>
              <Button disabled={busy || !editable} onClick={() => void action(async () => { setRevisions(await api<PromptAsset[]>(`/workflow-assets/${workflowId}/prompts/${prompt.id}/revisions`)); })}>查看共享提示词修订</Button>
              {!!revisions.length && <TextField select label="显式采用不可变修订" value="" onChange={e => { const chosen = revisions.find(r => r.revision === e.target.value); if (chosen) mutate(p => { p.prompts[prompt.id] = clone(chosen); }); }}><MenuItem value="">选择修订</MenuItem>{revisions.map(r => <MenuItem key={r.revision} value={r.revision}>{r.revision.slice(0, 20)}</MenuItem>)}</TextField>}
              </div><div hidden={stage !== 'test'} className="form-stack"><Divider /><Typography variant="subtitle2">固定样例</Typography><SampleFields value={sample()} onChange={value => update({ ...session, samples: { ...session.samples, [session.selected]: value } })} />
              <div className="button-row"><Button disabled={busy} onClick={() => void action(async () => { const data = body(); setPreview(await api(`/workflow-assets/${workflowId}/preview`, { method: 'POST', body: json(data) })); setPreviewBody(json(data)); })}>本地预览 messages</Button>
                <Button disabled={busy || !previewBody} variant="outlined" onClick={() => void action(async () => { const data = body(); if (json(data) !== previewBody) throw new Error('内容已变化，请重新预览本次发送的消息与参数。'); const value = await api<TestRecord>(`/workflow-assets/${workflowId}/tests`, { method: 'POST', body: previewBody }); setTest(value); setTests(await api<TestRecord[]>(`/workflow-assets/${workflowId}/tests`)); })}>发送样例并调用模型</Button></div>
              <Typography variant="caption">预览不调用模型。试运行只处理固定样例，不更新文章或执行输出；固定输入不保证输出逐字一致。</Typography>
              {preview !== null && <Accordion defaultExpanded disableGutters><AccordionSummary expandIcon={<ExpandMore />}>将发送的完整 messages、参数与修订</AccordionSummary><AccordionDetails><pre>{json(preview)}</pre></AccordionDetails></Accordion>}
              </div></> : <Typography color="text.secondary">此节点不调用模型。</Typography>}
            {stage === 'design' && session.package.node_config[session.selected] && <div className="form-stack"><Typography variant="subtitle2">此节点的输出配置</Typography>{Object.entries(session.package.node_config[session.selected]).flatMap(([group, values]) => Object.entries(values).map(([key, value]) => <TextField key={`${group}.${key}`} label={`${group}.${key}`} disabled={!editable || busy} value={value} onChange={e => mutate(p => { p.node_config[session.selected][group][key] = e.target.value; })} />))}<Typography variant="caption">相对路径沿用原配置文件目录。新配置仅随显式启用的新版本生效。</Typography></div>}
          </section>}
        </div>
        <div hidden={stage !== 'versions'}><Accordion defaultExpanded disableGutters><AccordionSummary expandIcon={<ExpandMore />}>流程历史与差异</AccordionSummary><AccordionDetails><div className="button-row">{session.catalog.versions.map(v => <Button key={v.id} disabled={busy || dirty} onClick={() => void action(() => history(v.id))}>{v.id.slice(0, 8)} · {formatTime(v.created_at)}</Button>)}</div><DocumentDiff before={json(session.catalog.active.payload)} after={json(session.package)} beforeLabel="当前启用版本" afterLabel={session.historyId ? '历史版本（只读）' : '当前草稿'} />
          <Button disabled={busy || !session.historyId || session.historyId === session.catalog.active.id} onClick={() => void action(async () => { adopt(await api<Catalog>(`/workflow-assets/${workflowId}/rollback`, { method: 'POST', body: json({ version_id: session.historyId, generation: session.catalog.active.generation }) }), workflowId); setNotice('启用指针已回退，只影响新运行。'); })}>回退启用到所选历史版本</Button>
          <Button disabled={!editable || busy || !!session.package.definition} onClick={() => void action(async () => { const defaults = await api<Pick<VersionPackage, 'model' | 'node_config'>>(`/workflow-assets/${workflowId}/defaults`); mutate(p => Object.assign(p, defaults)); setNotice('已把当前全局配置导入草稿，请核对、保存并启用。'); })}>把当前全局设置导入草稿</Button>
        </AccordionDetails></Accordion>
        </div><div hidden={stage !== 'test'}><Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>私有样例试运行记录（最近 100 条）</AccordionSummary><AccordionDetails>
          {!tests.length && <Typography>尚无试运行记录。先预览一个模型节点的固定样例。</Typography>}
          {tests.map(record => <div className="button-row" key={record.id}><Button onClick={() => void action(async () => { setTest(await api<TestRecord>(`/workflow-assets/${workflowId}/tests/${record.id}`)); })}>{record.node_id} · {testLabels[record.status]} · {record.prompt_revision.slice(0, 8)} · {formatTime(record.created_at)}</Button><Button onClick={() => void action(async () => { setCompareTest(await api<TestRecord>(`/workflow-assets/${workflowId}/tests/${record.id}`)); })}>选为比较基准</Button></div>)}
        </AccordionDetails></Accordion>
        {test && <section className="form-stack"><Typography variant="h6">试运行：{testLabels[test.status]}</Typography>{['running', 'stopping'].includes(test.status) && <Button disabled={test.status === 'stopping'} color="error" onClick={() => void action(async () => { setTest(await api<TestRecord>(`/workflow-assets/${workflowId}/tests/${test.id}/stop`, { method: 'POST', body: '{}' })); })}>停止试运行</Button>}{test.error !== undefined && <Alert severity="error">{json(test.error)}</Alert>}<NodeDiagnostics base={`/workflow-assets/${workflowId}/tests/${test.id}`} nodeId={test.node_id} refresh={test.status} />{compareTest && <DocumentDiff before={json(compareTest)} after={json(test)} beforeLabel={`基准 ${compareTest.prompt_revision.slice(0, 8)}`} afterLabel={`当前 ${test.prompt_revision.slice(0, 8)}`} />}</section>}
        </div>
      </>}
      <p className="session-hint">切页保留本地流程草稿。刷新前请保存草稿；已保存的测试记录可重新选择查看。</p>
    </div>
  </section>;
}
