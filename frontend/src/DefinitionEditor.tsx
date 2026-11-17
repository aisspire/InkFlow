import { navigate } from './navigation';
import SampleFields from './SampleFields';
import { useEffect, useRef, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Button, Checkbox, FormControlLabel, MenuItem, TextField, Typography } from '@mui/material';
import ExpandMore from '@mui/icons-material/ExpandMore';
import { api, formatTime, requestId } from './api';
import type { VersionPackage, PromptAsset } from './WorkflowStudio';
import type { Pending } from './types';
import { HumanAction } from './NodeDetails';
import DocumentDiff from './DocumentDiff';
import NodeDiagnostics from './NodeDiagnostics';

type Binding = { source: string; field: string };
export type Instance = { node_id: string; node_type_id: string; type_version: number; label: string; inputs: Record<string, Binding>; config: Record<string, unknown>; prompts: Record<string, PromptAsset>; model: Record<string, unknown> };
export type Definition = { schema_version: number; workflow_id: string; name: string; description: string; entry: string; ends: string[]; nodes: Instance[]; connections: { edge_id: string; source: string; target: string; port: string; when: { field: string; equals: boolean | string } | null }[]; canvas: Record<string, { x: number; y: number }>; capabilities: string[]; budget: { max_steps: number } };
type Schema = { type?: string; default?: unknown; enum?: unknown[]; minimum?: number; maximum?: number };
type NodeType = { node_type_id: string; type_version: number; label: string; inputs: Record<string, string>; outputs: Record<string, string>; capabilities: string[]; uses_model: boolean; side_effect: boolean; controlled_loop: boolean; config_schema: { properties: Record<string, Schema> }; implementation: string; limits: unknown; interactions: unknown };
export type Profile = { profile_id: string; revision: string; generation: number; payload: Record<string, string>; revisions: { revision: string; created_at: string }[]; users: { workflow_id: string; node_id: string; revision: string }[] };
type Trial = { id: string; status: string; pending?: Pending; state?: unknown; error?: string; created_at: string; source: unknown };
type Issue = { field: string; message: string; node_id?: string; edge_id?: string };
const json = (v: unknown) => JSON.stringify(v, null, 2);

export function definitionGraph(p: VersionPackage) {
  const d = p.definition!;
  const visited = new Set<string>(), order: string[] = [];
  const visit = (id: string) => {
    if (visited.has(id) || !d.nodes.some(n => n.node_id === id)) return;
    visited.add(id);
    d.connections.filter(e => e.source === id).forEach(e => visit(e.target));
    order.push(id);
  };
  visit(d.entry);
  const ordered = [...order.reverse().map(id => d.nodes.find(n => n.node_id === id)!), ...d.nodes.filter(n => !visited.has(n.node_id))];
  return { id: d.workflow_id, name: d.name, description: d.description,
    nodes: ordered.map((n, i) => ({ id: n.node_id, label: n.label, node_type_id: n.node_type_id, description: n.node_type_id, human: ['review-article', 'confirm-local-output', 'redaction', 'full-check', 'generate-article'].includes(n.node_type_id), position: d.canvas[n.node_id] ?? { x: i % 3 * 290, y: Math.floor(i / 3) * 170 } })),
    edges: [...d.connections.map(e => ({ id: e.edge_id, source: e.source, target: e.target })), ...d.nodes.filter(n => ['full-check', 'redaction', 'generate-article', 'confirm-local-output'].includes(n.node_type_id)).map(n => ({ id: n.node_id + '-internal', source: n.node_id, target: n.node_id }))] };
}

export default function DefinitionEditor({ value, selected, editable, busy, onChange, onSelect, view }: { view: 'design' | 'test'; value: VersionPackage; selected: string; editable: boolean; busy: boolean; onChange: (change: (p: VersionPackage) => void) => void; onSelect: (id: string) => void }) {
  const [types, setTypes] = useState<NodeType[]>([]), [profiles, setProfiles] = useState<Profile[]>([]);
  const [typeId, setTypeId] = useState('snapshot');
  const [error, setError] = useState(''), [issues, setIssues] = useState<Issue[]>([]), [plan, setPlan] = useState<unknown>(null);
  const [working, setWorking] = useState(false);
  const [sample, setSample] = useState('# 固定样例\n\n<X value={{a: 1}} />\n'), [allowModel, setAllowModel] = useState(false);
  const samples = useRef(new Map<string, string>());
  const [trial, setTrial] = useState<Trial | null>(null), [trials, setTrials] = useState<Trial[]>([]), [compare, setCompare] = useState<Trial | null>(null);
  const [modelSample, setModelSample] = useState<Record<string, string>>({}), [modelPreview, setModelPreview] = useState<unknown>(null), [modelTest, setModelTest] = useState<{ id: string; status: string } | null>(null);
  const d = value.definition!, node = d.nodes.find(n => n.node_id === selected), type = types.find(t => t.node_type_id === node?.node_type_id);
  const locked = !editable || busy || working;
  const base = `/workflow-assets/${value.workflow_id}`;
  useEffect(() => { void Promise.all([api<NodeType[]>('/node-types'), api<Profile[]>('/publish-profiles')]).then(([t, p]) => { setTypes(t); setProfiles(p); }).catch(e => setError(e.message)); }, []);
  useEffect(() => { const refresh = () => { void api<Profile[]>('/publish-profiles').then(setProfiles).catch(e => setError(e.message)); }; window.addEventListener('inkflow-profiles-changed', refresh); return () => window.removeEventListener('inkflow-profiles-changed', refresh); }, []);
  useEffect(() => { setTrial(null); setCompare(null); setPlan(null); setIssues([]); setSample(samples.current.get(value.workflow_id) ?? '# 固定样例\n\n<X value={{a: 1}} />\n'); void api<Trial[]>(`${base}/definition-tests`).then(setTrials).catch(e => setError(e.message)); }, [base]);
  useEffect(() => {
    if (!trial || trial.status !== 'running') return;
    const timer = setInterval(() => { void api<Trial>(`${base}/definition-tests/${trial.id}`).then(setTrial).catch(e => setError(e.message)); }, 600);
    return () => clearInterval(timer);
  }, [trial, base]);
  useEffect(() => {
    if (!modelTest || modelTest.status !== 'running') return;
    const timer = setInterval(() => { void api<{ id: string; status: string }>(`${base}/tests/${modelTest.id}`).then(setModelTest).catch(e => setError(e.message)); }, 600);
    return () => clearInterval(timer);
  }, [modelTest, base]);
  async function task(fn: () => Promise<void>) { setWorking(true); setError(''); try { await fn(); } catch (e) { setError((e as Error).message); setIssues((e as Error & { issues?: Issue[] }).issues ?? []); } finally { setWorking(false); } }
  function edit(fn: (definition: Definition) => void) { setPlan(null); onChange(p => { fn(p.definition!); p.compiled = null; p.graph = definitionGraph(p); }); }
  function editNode(fn: (n: Instance) => void) { edit(def => { const n = def.nodes.find(n => n.node_id === selected); if (n) fn(n); }); }
  function field(key: string, current: unknown, schema: Schema, change: (v: unknown) => void, prefix = '') {
    const label = prefix + key;
    if (schema.type === 'boolean' || typeof current === 'boolean') return <FormControlLabel key={key} control={<Checkbox disabled={locked} checked={current === true} onChange={e => change(e.target.checked)} />} label={label} />;
    if (schema.enum) return <TextField select key={key} label={label} disabled={locked} value={String(current ?? '')} onChange={e => change(e.target.value)}>{schema.enum.map(v => <MenuItem key={String(v)} value={String(v)}>{String(v)}</MenuItem>)}</TextField>;
    return <TextField key={key} label={label} disabled={locked} value={Array.isArray(current) ? current.join(', ') : current ?? ''} type={schema.type === 'integer' || schema.type === 'number' || typeof current === 'number' ? 'number' : 'text'} slotProps={{ htmlInput: { min: schema.minimum, max: schema.maximum } }} onChange={e => change(schema.type === 'array' || Array.isArray(current) ? e.target.value.split(',').map(s => s.trim()).filter(Boolean) : schema.type === 'integer' || schema.type === 'number' || typeof current === 'number' ? Number(e.target.value) : e.target.value)} />;
  }
  return <section className="form-stack definition-editor" aria-label="流程定义编辑">
    <TextField select label="当前节点" value={node?.node_id ?? ''} onChange={e => onSelect(e.target.value)}>{d.nodes.map(n => <MenuItem key={n.node_id} value={n.node_id}>{n.label}</MenuItem>)}</TextField>
    {error && <Alert severity="error">{error}</Alert>}
    <div hidden={view !== 'design'} className="form-stack"><details className="flow-settings"><summary>流程级配置</summary><div className="form-stack"><TextField label="流程名称" value={d.name} disabled={locked} onChange={e => edit(def => { def.name = e.target.value; })} />
    <TextField label="流程说明" value={d.description} disabled={locked} onChange={e => edit(def => { def.description = e.target.value; })} />
    <div className="button-row"><TextField select label="节点目录" value={typeId} disabled={locked} onChange={e => setTypeId(e.target.value)}>{types.map(t => <MenuItem key={t.node_type_id} value={t.node_type_id}>{t.label}</MenuItem>)}</TextField><Button disabled={locked} onClick={() => void task(async () => { const n = await api<Instance>(`/node-types/${typeId}/instance`); n.node_id = 'n' + requestId().replaceAll('-', '').slice(0, 10); edit(def => { def.nodes.push(n); def.capabilities = [...new Set([...def.capabilities, ...(types.find(t => t.node_type_id === typeId)?.capabilities ?? [])])]; if (typeId === 'end') def.ends.push(n.node_id); }); onSelect(n.node_id); })}>添加节点</Button></div>
    <TextField select label="入口节点" value={d.entry} disabled={locked} onChange={e => edit(def => { def.entry = e.target.value; })}>{d.nodes.map(n => <MenuItem key={n.node_id} value={n.node_id}>{n.label}</MenuItem>)}</TextField>
    <TextField label="执行步数预算" type="number" value={d.budget.max_steps} disabled={locked} onChange={e => edit(def => { def.budget.max_steps = Number(e.target.value); })} />
    </div></details><Typography variant="h6">节点配置</Typography>
    {node && type && <>
      <Typography variant="h6">{type.label}</Typography><Typography variant="caption">{node.node_id} · 类型版本 {node.type_version} · {type.uses_model ? '调用模型' : '不调用模型'} · {type.side_effect ? '会写入本地文件' : '无文件输出'}</Typography>
      <TextField label="节点名称" value={node.label} disabled={locked} onChange={e => editNode(n => { n.label = e.target.value; })} />
      <Typography variant="subtitle2">输入绑定</Typography>
      {Object.entries(type.inputs).map(([port, required]) => <TextField select key={port} label={`${port} · ${required}`} disabled={locked} value={node.inputs[port] ? `${node.inputs[port].source}.${node.inputs[port].field}` : ''} onChange={e => { const [source, field] = e.target.value.split('.'); editNode(n => { n.inputs[port] = { source, field }; }); }}>
        <MenuItem value="">请选择来源</MenuItem>{required === 'text' && <MenuItem value="$input.text">输入文档快照</MenuItem>}
        {d.nodes.filter(n => n.node_id !== node.node_id).flatMap(n => Object.entries(types.find(t => t.node_type_id === n.node_type_id)?.outputs ?? {}).filter(([, t]) => t === required).map(([name]) => <MenuItem key={`${n.node_id}.${name}`} value={`${n.node_id}.${name}`}>{n.label} · {name}</MenuItem>))}
      </TextField>)}
      <Typography variant="subtitle2">连接与分支</Typography>
      {d.connections.filter(e => e.source === node.node_id).map(edge => <div className="connection-row form-stack" key={edge.edge_id}>
        <TextField select label="下一个节点" value={edge.target} disabled={locked} onChange={e => edit(def => { def.connections.find(x => x.edge_id === edge.edge_id)!.target = e.target.value; })}>{d.nodes.filter(n => n.node_id !== node.node_id).map(n => <MenuItem key={n.node_id} value={n.node_id}>{n.label}</MenuItem>)}</TextField>
        <TextField select label="分支条件" value={edge.when?.field ?? ''} disabled={locked} onChange={e => edit(def => { def.connections.find(x => x.edge_id === edge.edge_id)!.when = e.target.value ? { field: e.target.value, equals: type.outputs[e.target.value] === 'boolean' ? true : '' } : null; })}><MenuItem value="">兜底（未匹配其它条件）</MenuItem>{Object.entries(type.outputs).filter(([, t]) => ['boolean', 'text'].includes(t)).map(([name]) => <MenuItem key={name} value={name}>{name} 等于</MenuItem>)}</TextField>
        {edge.when && field('条件值', edge.when.equals, { type: type.outputs[edge.when.field] }, v => edit(def => { def.connections.find(x => x.edge_id === edge.edge_id)!.when!.equals = v as boolean | string; }))}
        <Button color="error" disabled={locked} onClick={() => edit(def => { def.connections = def.connections.filter(e => e.edge_id !== edge.edge_id); })}>删除此连接</Button>
      </div>)}
      {node.node_type_id !== 'end' && <Button disabled={locked || d.nodes.length < 2} onClick={() => edit(def => { def.connections.push({ edge_id: 'e' + requestId(), source: node.node_id, target: d.nodes.find(n => n.node_id !== node.node_id)!.node_id, port: 'next', when: null }); })}>添加出口连接</Button>}
      {node.node_type_id === 'confirm-local-output' ? <>
        <Typography variant="subtitle2">本地发布配置</Typography>
        <TextField select label="固定发布配置修订" value={node.config.profile_id ? `${node.config.profile_id}:${node.config.revision}` : ''} disabled={locked} onChange={e => { const [profile_id, revision] = e.target.value.split(':'); editNode(n => { n.config = { profile_id, revision }; }); }}><MenuItem value="">请选择配置</MenuItem>{profiles.flatMap(p => p.revisions.map(r => <MenuItem key={p.profile_id + r.revision} value={`${p.profile_id}:${r.revision}`}>{p.payload.name} · {r.revision.slice(0, 8)} {r.revision === p.revision ? '（最新）' : ''}</MenuItem>))}</TextField>
        <Button onClick={() => navigate({ page: 'settings' })}>管理共享发布配置</Button><Typography variant="caption">修改共享配置不会更新此节点。请显式选择固定修订并保存、启用流程。</Typography>
      </> : Object.entries(type.config_schema.properties).map(([key, schema]) => field(key, node.config[key], schema, v => editNode(n => { n.config[key] = v; })))}
      {Object.entries(node.prompts).map(([pid, prompt]) => <Accordion key={pid} disableGutters><AccordionSummary expandIcon={<ExpandMore />}>此实例提示词：{pid} · {prompt.revision.slice(0, 8)}</AccordionSummary><AccordionDetails><div className="form-stack">{prompt.messages.map((m, i) => <TextField key={i} label={`${m.role} 消息模板`} multiline minRows={3} maxRows={16} value={m.content} disabled={locked} onChange={e => editNode(n => { n.prompts[pid].messages[i].content = e.target.value; })} />)}<pre>{json(prompt.variables)}</pre><Typography variant="caption">解析器与输出协议固定；变量只替换一次。</Typography>

      </div></AccordionDetails></Accordion>)}
      {type.uses_model && <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>此实例的模型参数</AccordionSummary><AccordionDetails><div className="form-stack">{Object.entries(node.model).map(([key, v]) => field(key, v, {}, val => editNode(n => { n.model[key] = val; })))}</div></AccordionDetails></Accordion>}
      <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>类型契约与画布位置</AccordionSummary><AccordionDetails><pre>{json({ inputs: type.inputs, outputs: type.outputs, capabilities: type.capabilities, interactions: type.interactions, limits: type.limits, implementation: type.implementation })}</pre>{['x', 'y'].map(axis => <TextField key={axis} label={`画布 ${axis}（不改变执行顺序）`} type="number" value={d.canvas[node.node_id]?.[axis as 'x' | 'y'] ?? 0} disabled={locked} onChange={e => edit(def => { def.canvas[node.node_id] = { ...(def.canvas[node.node_id] ?? { x: 0, y: 0 }), [axis]: Number(e.target.value) }; })} />)}</AccordionDetails></Accordion>
      <Button color="error" disabled={locked} onClick={() => { edit(def => { def.nodes = def.nodes.filter(n => n.node_id !== selected); def.connections = def.connections.filter(e => e.source !== selected && e.target !== selected); def.ends = def.ends.filter(n => n !== selected); delete def.canvas[selected]; }); onSelect(d.nodes.find(n => n.node_id !== selected)?.node_id ?? ''); }}>删除节点及关联连线</Button>
    </>}
    </div><div hidden={view !== 'test'} className="form-stack">
    {node && Object.entries(node.prompts).map(([pid, prompt]) => <section key={pid} className="form-stack"><Typography variant="h6">模型样例 · {pid}</Typography>        <SampleFields value={modelSample[node.node_id + pid] ?? json(Object.fromEntries(Object.entries(prompt.variables).map(([key, spec]) => [key, spec.sample])))} onChange={value => setModelSample({ ...modelSample, [node.node_id + pid]: value })} />
        <Button onClick={() => void task(async () => { const variables = JSON.parse(modelSample[node.node_id + pid] ?? json(Object.fromEntries(Object.entries(prompt.variables).map(([key, spec]) => [key, spec.sample])))); setModelPreview(await api(`${base}/preview`, { method: 'POST', body: json({ node_id: node.node_id, prompt_id: pid, package: value, variables }) })); })}>本地预览此实例 messages</Button>
        <Button onClick={() => void task(async () => { const variables = JSON.parse(modelSample[node.node_id + pid] ?? json(Object.fromEntries(Object.entries(prompt.variables).map(([key, spec]) => [key, spec.sample])))); setModelTest(await api(`${base}/tests`, { method: 'POST', body: json({ node_id: node.node_id, prompt_id: pid, package: value, variables }) })); })}>显式调用此实例模型样例</Button></section>)}
    <Button variant="outlined" disabled={working || busy} onClick={() => void task(async () => { setIssues([]); setPlan(await api(`${base}/plan`, { method: 'POST', body: json({ package: value }) })); })}>校验并预览执行计划</Button>
    {issues.map((issue, i) => <Alert key={i} severity="error" action={issue.node_id ? <Button onClick={() => onSelect(issue.node_id!)}>定位节点</Button> : undefined}>{issue.field}：{issue.message}</Alert>)}
    {plan !== null && <Accordion defaultExpanded><AccordionSummary expandIcon={<ExpandMore />}>执行计划（未调用模型、未写文件）</AccordionSummary><AccordionDetails><pre>{json(plan)}</pre></AccordionDetails></Accordion>}
    <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>流程模拟试运行</AccordionSummary><AccordionDetails><div className="form-stack"><TextField label="固定样例正文" multiline minRows={4} value={sample} onChange={e => { setSample(e.target.value); samples.current.set(value.workflow_id, e.target.value); }} /><FormControlLabel control={<Checkbox checked={allowModel} onChange={e => setAllowModel(e.target.checked)} />} label="允许本次样例中的模型节点调用模型" /><Button disabled={working} onClick={() => void task(async () => { setTrial(await api(`${base}/definition-tests`, { method: 'POST', body: json({ package: value, text: sample, allow_model: allowModel }) })); setTrials(await api(`${base}/definition-tests`)); })}>开始流程模拟</Button><Typography variant="caption">文档采用与全部文件输出均已隔离；人工节点仍需逐次提交模拟动作。</Typography>
      {trial && <><Alert severity={trial.status === 'failed' ? 'error' : 'info'}>模拟状态：{trial.status} {trial.error}</Alert>{trial.pending && <HumanAction key={trial.pending.id} pending={trial.pending} beforeText={sample} busy={working} onAction={async (action, payload = {}) => { setTrial(await api(`${base}/definition-tests/${trial.id}/actions`, { method: 'POST', body: json({ request_id: requestId(), pending_id: trial.pending!.id, action, payload: action === 'accept' ? { ...payload, editor: {} } : payload }) })); }} />}<NodeDiagnostics base={`${base}/definition-tests/${trial.id}`} refresh={trial.status} /><pre>{json(trial.state)}</pre></>}
      {trials.map(t => <div className="button-row" key={t.id}><Button onClick={() => void task(async () => setTrial(await api(`${base}/definition-tests/${t.id}`)))}>{formatTime(t.created_at)} · {t.status}</Button><Button onClick={() => void task(async () => setCompare(await api(`${base}/definition-tests/${t.id}`)))}>设为比较基准</Button></div>)}{compare && trial && <DocumentDiff before={json(compare)} after={json(trial)} beforeLabel="基准模拟" afterLabel="当前模拟" />}
    </div></AccordionDetails></Accordion>
    {modelPreview !== null && <details><summary>技术详情：完整模型消息</summary><pre>{json(modelPreview)}</pre></details>}{modelTest && <NodeDiagnostics base={`${base}/tests/${modelTest.id}`} nodeId={node?.node_id} refresh={modelTest.status} />}
    </div>
  </section>;
}
