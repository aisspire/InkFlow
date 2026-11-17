import CaseFields from './CaseFields';
import { HumanAction } from './NodeDetails';
import type { Pending } from './types';
import { navigate, openAI, returnFromAI, useRoute, type AIRepair } from './navigation';
import { useEffect, useRef, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Button, Checkbox, FormControlLabel, MenuItem, TextField, Typography } from '@mui/material';
import ExpandMore from '@mui/icons-material/ExpandMore';
import { api, requestId } from './api';
import WorkflowCanvas from './WorkflowCanvas';
import DocumentDiff from './DocumentDiff';
import type { VersionPackage } from './WorkflowStudio';

type Repair = AIRepair;
type Result = { id?: string; pending_id?: string | null; status: string; passed?: boolean; assertions?: Record<string, boolean>; metrics?: unknown; failed_attempts?: unknown[]; error?: string };
type Proposal = { document_id?: string; id: string; workflow_id: string; goal: string; sample: string; status: string; revision: number; generation: number; applied_revision?: number; activated_version?: string; base: VersionPackage; package: VersionPackage | null; proposed_definition?: unknown; repair_of?: Repair; input_refs?: { source?: { snapshot_id: string }; document: { document_id: string } }; messages?: unknown; raw_output?: string; usage?: unknown; diagnostic?: unknown; model_attempts?: unknown[]; effect_review?: unknown; analysis?: unknown; errors?: unknown[]; diff?: unknown; error?: string; capabilities: string[]; profile_refs: unknown; verification?: { samples?: unknown[]; status: string; results: { name: string; baseline: Result; candidate: Result }[] } };
const json = (v: unknown) => JSON.stringify(v, null, 2);
const labels: Record<string, string> = { generating: '模型处理中', ready: '待审阅', invalid: '无效草稿', failed: '失败', applied: '已采用 · 待验证启用', activated: '已启用', interrupted: '重启中断', cancelled: '已取消' };
const capabilities = { human: '人工审阅', candidate: '文章候选', model: '模型调用', 'local-blog': '本地博客', 'local-review': '已有审阅稿目录', 'local-report': '已有报告目录' };

function Simulation({ workflowId, result, refresh }: { workflowId: string; result: Result; refresh: () => void }) {
  const [pending, setPending] = useState<Pending | null>(null), [error, setError] = useState('');
  const base = '/workflow-assets/' + workflowId + '/definition-tests/' + result.id;
  useEffect(() => { if (result.status !== 'waiting') { setPending(null); return; } let live = true;
    void api<{ pending: Pending }>(base).then(t => { if (live) setPending(t.pending); }).catch(e => setError(e.message));
    return () => { live = false; }; }, [base, result.status, result.pending_id]);
  return <div className="form-stack"><Typography variant="caption">{result.status} {result.passed === true ? '· 样例通过' : ''}</Typography>{result.error && <Alert severity="error">{result.error}</Alert>}
    {result.assertions && <div>{Object.entries(result.assertions).map(([key, passed]) => <p key={key}>{key}：{passed ? '通过' : '未通过'}</p>)}</div>}
    {pending && <HumanAction key={pending.id} pending={{ ...pending, simulated: true }} beforeText={pending.before_text ?? ''} busy={false} onAction={async (action, payload = {}) => { await api(base + '/actions', { method: 'POST', body: json({ request_id: requestId(), pending_id: pending.id, action, payload: action === 'accept' ? { ...payload, editor: {} } : payload }) }); refresh(); }} />}
    {['running', 'stopping'].includes(result.status) && <Button onClick={() => void api(base + '/stop', { method: 'POST', body: '{}' }).then(refresh).catch(e => setError(e.message))}>停止此模拟</Button>}
    <details><summary>模拟技术详情</summary><pre>{json(result)}</pre></details>{error && <Alert severity="error">{error}</Alert>}</div>;
}

export default function AIAssistant({ active, isDirty, onOpenWorkflow }: { active: boolean; isDirty: (id: string) => boolean; onOpenWorkflow?: (id: string) => void }) {
  const route = useRoute();
  const open = active && !!route.ai;
  let context: { repair?: Repair; documentId?: string } = {};
  try { context = JSON.parse(sessionStorage.getItem('inkflow-ai-repair') || '{}'); } catch { /* no session source */ }
  const repair = route.ai === 'repair' ? context.repair : undefined, documentId = repair ? context.documentId : undefined;
  const step = Math.max(0, Math.min(3, Number(route.step) || 0));
  const [name, setName] = useState('AI 新流程');
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const [goal, setGoal] = useState(repair ? '分析此失败尝试，提出可验证的提示词、参数或声明式修复，并说明依据和不确定性。' : '');
  const [sample, setSample] = useState('示例文章正文'), [constraints, setConstraints] = useState('保留正文，所有输出须人工确认');
  const [grants, setGrants] = useState(['human', 'candidate', 'model']);
  const [profiles, setProfiles] = useState<{ profile_id: string; revision: string; payload: { name: string } }[]>([]), [profileIds, setProfileIds] = useState<string[]>([]);
  const [records, setRecords] = useState<Proposal[]>([]), [item, setItem] = useState<Proposal | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [cases, setCases] = useState(''), [allowModel, setAllowModel] = useState(false), [selected, setSelected] = useState('');
  const current = useRef('');
  useEffect(() => {
    const edited = (event: Event) => { const id = (event as CustomEvent<{ workflowId: string }>).detail.workflowId; setInvalid(old => ({ ...old, [id]: true })); };
    window.addEventListener('inkflow-definition-edited', edited); return () => window.removeEventListener('inkflow-definition-edited', edited);
  }, []);
  useEffect(() => { if (!open) return; if (repair) { setName('AI 修复流程'); setGoal('分析此失败尝试，提出可验证的提示词、参数或声明式修复，并说明依据和不确定性。'); } }, [open, repair?.attempt_id]);
  const headers = documentId ? { 'X-InkFlow-Document': documentId } : undefined;
  function proposalHeaders(id: string) { const doc = (item?.id === id ? item.input_refs?.document.document_id : records.find(r => r.id === id)?.document_id) ?? documentId; return doc ? { 'X-InkFlow-Document': doc } : undefined; }
  async function load(id: string) { current.current = id; const value = await api<Proposal>(`/ai/proposals/${id}`, { headers: proposalHeaders(id) }); if (current.current === id) setItem(value); }
  async function task(fn: () => Promise<void>) { const trigger = document.activeElement as HTMLElement | null; setBusy(true); setError(''); try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); requestAnimationFrame(() => { if (trigger?.isConnected && document.activeElement === document.body) trigger.focus(); }); } }
  async function post(suffix: string, body: unknown = {}) { if (!item) return; const id = item.id; const value = await api<Proposal>(`/ai/proposals/${id}/${suffix}`, { method: 'POST', body: json(body), headers: proposalHeaders(id) }); if (current.current === id) setItem(value); }
  useEffect(() => { if (!open) return; void Promise.all([api<Proposal[]>('/ai/proposals', { headers }), api<typeof profiles>('/publish-profiles')]).then(([r, p]) => { setRecords(r); setProfiles(p); }).catch(e => setError(e.message)); }, [open, item?.id]);
  useEffect(() => {
    if (!open || !item || !(item.status === 'generating' || item.verification?.status === 'running')) return;
    const id = item.id;
    let active = true;
    const timer = setInterval(() => { void api<Proposal>(`/ai/proposals/${id}`, { headers: proposalHeaders(id) }).then(value => { if (active && current.current === id) setItem(value); }).catch(e => { if (active && current.current === id) setError(e.message); }); }, 900);
    return () => { active = false; clearInterval(timer); };
  }, [open, item?.id, item?.status, item?.verification?.status]);
  useEffect(() => { if (!open || !route.proposal || current.current === route.proposal && item?.id === route.proposal) return; void task(() => load(route.proposal)); }, [open, route.proposal, records]);
  useEffect(() => { setCases(item ? json(item.verification?.samples ?? [{ name: '固定样例', text: item.sample, actions: [], contains: [], excludes: [] }]) : ''); }, [item?.id]);
  const localInvalid = !!item && (invalid[item.workflow_id] || isDirty(item.workflow_id));
  let casesChanged = false; try { casesChanged = !!item?.verification && json(JSON.parse(cases || 'null')) !== json(item.verification.samples); } catch { casesChanged = true; }
  return <><Button hidden={!!route.ai} onClick={() => openAI()}>AI 创建流程</Button>{!route.ai && item && <Button onClick={() => navigate({ ai: item.repair_of ? 'repair' : 'create', proposal: item.id, step: '1' })}>继续 AI 审阅</Button>}<section hidden={!open} className="workflow-studio ai-assistant" aria-label="AI 创建与修复">
    <div className="page-heading"><Typography variant="h6">{repair ? 'AI 诊断与修复' : 'AI 创建与验证'}</Typography><Button onClick={returnFromAI}>返回来源页面</Button></div>
    <div className="page-tabs" role="group" aria-label="AI 阶段">{['描述目标', '审阅方案', '样例测试', '启用'].map((label, index) => <Button key={label} aria-pressed={step === index} disabled={index > 0 && !item} onClick={() => navigate({ step: String(index) })}>{index + 1}. {label}</Button>)}</div>
    <TextField select label="已保存的 AI 提案" value={item?.id ?? ''} onChange={e => navigate({ proposal: e.target.value, step: '1' })}><MenuItem value="">选择历史</MenuItem>{[...records.filter(r => r.id !== item?.id), ...(item ? [item] : [])].map(r => <MenuItem key={r.id} value={r.id}>{labels[r.status] ?? r.status} · {r.goal.slice(0, 32)}</MenuItem>)}</TextField>
    {error && <Alert severity="error">{error}</Alert>}{notice && <Alert severity="info" onClose={() => setNotice('')}>{notice}</Alert>}
    <div className="ai-layout"><section hidden={step !== 0} className="form-stack">
      <Typography variant="body2">生成独立流程草稿。授权以本次勾选为准；模型结果经审阅、样例与模拟后显式启用。</Typography>
      {repair && <Typography variant="caption" className="studio-identifier">失败尝试 {repair.attempt_id}</Typography>}
      <TextField label="AI 流程名称" value={name} onChange={e => setName(e.target.value)} /><TextField label="用户目标" multiline minRows={3} value={goal} onChange={e => setGoal(e.target.value)} /><TextField label="示例输入" multiline minRows={3} value={sample} onChange={e => setSample(e.target.value)} /><TextField label="输出约束" multiline minRows={2} value={constraints} onChange={e => setConstraints(e.target.value)} />
      <fieldset className="ai-grants"><legend>允许的能力</legend>{Object.entries(capabilities).map(([id, label]) => <FormControlLabel key={id} label={label} control={<Checkbox checked={grants.includes(id)} onChange={(_, checked) => setGrants(checked ? [...grants, id] : grants.filter(v => v !== id))} />} />)}</fieldset>
      {grants.includes('local-blog') && <TextField select label="授权已有发布配置" slotProps={{ select: { multiple: true } }} value={profileIds} onChange={e => setProfileIds(typeof e.target.value === 'string' ? e.target.value.split(',') : e.target.value)}>{profiles.map(p => <MenuItem key={p.profile_id} value={p.profile_id}>{p.payload.name} · {p.revision.slice(0, 8)}</MenuItem>)}</TextField>}
      <Button variant="contained" disabled={busy || !goal.trim() || item?.status === 'generating'} onClick={() => void task(async () => { const value = await api<Proposal>('/ai/proposals', { method: 'POST', headers, body: json({ name, goal, sample, constraints, capabilities: grants, profile_refs: profiles.filter(p => profileIds.includes(p.profile_id)).map(p => ({ profile_id: p.profile_id, revision: p.revision })), repair_of: repair ?? null }) }); current.current = value.id; setItem(value); navigate({ proposal: value.id, step: '1' }); setNotice('请求已固定；可返回继续编辑，结果不会自动覆盖草稿。'); })}>发送目标并调用 AI</Button>

    </section><section hidden={step === 0} className="form-stack ai-results" aria-live="polite">
      {!item ? <Typography>填写目标后生成提案，或查看已保存的分析和验证记录。</Typography> : <>
        <Typography variant="h6">{labels[item.status] ?? item.status}</Typography>{item.error && <Alert severity="error">{item.error}</Alert>}
        {item.status === 'generating' && <Button onClick={() => void task(() => post('cancel'))}>取消生成</Button>}
        {item.analysis != null && step === 1 && <><Typography variant="subtitle2">模型分析（待人工确认）</Typography><div className="analysis-summary">{typeof item.analysis === 'string' ? item.analysis : Object.entries(item.analysis as Record<string, unknown>).filter(([, v]) => v != null).map(([key, v]) => <section key={key}><strong>{({ summary: '分析摘要', changes: '修改内容', uncertainty: '不确定性', uncertainties: '不确定性', rationale: '修改依据', evidence: '依据', category: '问题类型' } as Record<string, string>)[key] ?? key}</strong><p>{typeof v === 'string' ? v : Array.isArray(v) ? v.map(x => typeof x === 'string' ? x : json(x)).join('；') : json(v)}</p></section>)}</div></>}
        {!!item.errors?.length && <Alert severity="error"><pre>{json(item.errors)}</pre></Alert>}
        <Accordion><AccordionSummary expandIcon={<ExpandMore />}>技术详情：固定输入、权限与模型记录</AccordionSummary><AccordionDetails><pre>{json({ repair_of: item.repair_of, input_refs: item.input_refs, capabilities: item.capabilities, profile_refs: item.profile_refs, original_model_attempts: item.model_attempts, effect_review: item.effect_review, messages: item.messages, raw_output: item.raw_output, usage: item.usage, diagnostic: item.diagnostic })}</pre></AccordionDetails></Accordion>
        <div hidden={step !== 1}>{item.package?.definition && <section aria-label="方案修改摘要"><h3>方案修改</h3><p>{item.package.definition.name} · {item.package.definition.nodes.length} 个节点</p><ul>{item.package.definition.nodes.filter(n => json(n) !== json(item.base.definition?.nodes.find(b => b.node_id === n.node_id))).map(n => <li key={n.node_id}>{item.base.definition?.nodes.some(b => b.node_id === n.node_id) ? '修改' : '新增'}：{n.label}</li>)}{item.base.definition?.nodes.filter(n => !item.package!.definition!.nodes.some(b => b.node_id === n.node_id)).map(n => <li key={n.node_id}>移除：{n.label}</li>)}</ul></section>}{item.package && <div className="ai-graph"><WorkflowCanvas workflow={item.package.graph} run={null} selected={selected} onSelect={setSelected} /></div>}
        {!!(item.package || item.proposed_definition) && <><details><summary>技术详情：定义差异</summary><DocumentDiff before={json(item.base.definition)} after={json(item.package?.definition ?? item.proposed_definition)} beforeLabel="原始定义" afterLabel="AI 提案" /><pre>{json(item.diff)}</pre></details></>}
        <div className="button-row"><Button disabled={busy || item.status !== 'ready'} onClick={() => void task(() => post('apply', { revision: item.revision, generation: item.generation }))}>已核对差异，采用为草稿</Button>
          <Button disabled={!['applied', 'activated'].includes(item.status)} onClick={() => { if (onOpenWorkflow) onOpenWorkflow(item.workflow_id); else window.dispatchEvent(new CustomEvent('inkflow-open-studio', { detail: { workflowId: item.workflow_id } })); navigate({ ai: '', stage: 'design', workflow: item.workflow_id }); }}>打开此流程设计器</Button>
          <Button disabled={busy || isDirty(item.workflow_id) || ['generating', 'cancelled', 'activated', 'interrupted'].includes(item.status)} onClick={() => void task(async () => { const view = await api<{ draft: { revision: number } | null; active: { generation: number } }>(`/workflow-assets/${item.workflow_id}`); if (!view.draft) throw new Error('请先在设计器保存草稿。'); await post('revise', { revision: view.draft.revision, generation: view.active.generation }); setInvalid(old => ({ ...old, [item.workflow_id]: false })); })}>采用人工保存的草稿并重置验证</Button></div><Button onClick={() => navigate({ step: '2' })}>下一步：样例测试</Button></div>
        {localInvalid && <Alert severity="warning">人工修改后，验证已失效。请在设计器保存，再于“审阅方案”采用人工保存的草稿并重新验证。</Alert>}
        <div hidden={step !== 2}>{item.package && <><CaseFields value={cases} onChange={setCases} /><FormControlLabel label="允许验证中的模型调用（可能计费）" control={<Checkbox checked={allowModel} onChange={(_, v) => setAllowModel(v)} />} />
          <Button disabled={busy || localInvalid || !['ready', 'applied'].includes(item.status) || item.verification?.status === 'running'} onClick={() => void task(async () => { const samples = JSON.parse(cases); if (Array.isArray(samples)) for (const sample of samples) { if (sample && typeof sample === 'object') for (const key of ['contains', 'excludes']) if (Array.isArray(sample[key])) sample[key] = sample[key].filter((value: unknown) => typeof value !== 'string' || value.trim().length > 0); } setCases(json(samples)); await post('verify', { samples, allow_model: allowModel }); })}>校验并比较原／新版本模拟</Button>
          <Typography variant="caption">模拟不保存或发布文章。人工节点逐次确认模拟待办；contains / excludes 检查节点输出。模型结果可能变化。</Typography>
        </>}
        {item.verification && <><Typography variant="subtitle2">验证结果：{({ passed: '通过', failed: '失败', running: '进行中／等待模拟待办' })[item.verification.status]}</Typography>{item.verification.results.map((r, i) => <div key={`${item.id}-${i}-${r.candidate.id}`}><Typography>{r.name} · 原版本</Typography><Simulation workflowId={item.workflow_id} result={r.baseline} refresh={() => void load(item.id)} /><Typography>{r.name} · 新版本</Typography><Simulation workflowId={item.workflow_id} result={r.candidate} refresh={() => void load(item.id)} /></div>)}</>}
        <Button onClick={() => navigate({ step: '3' })}>下一步：启用</Button></div>
        <div hidden={step !== 3}><Typography variant="body2">启用条件</Typography><ul><li>{item.status === 'applied' || item.status === 'activated' ? '已显式采用草稿' : '尚未采用草稿，请返回审阅方案'}</li><li>{item.verification?.status === 'passed' ? '样例验证通过' : '样例尚未通过，请返回测试'}</li><li>{localInvalid || casesChanged ? '内容已变化，需要重新验证' : '本页未修改验证内容'}</li></ul>{casesChanged && <Alert severity="warning">样例或断言已修改，请重新验证。</Alert>}<Button variant="contained" disabled={busy || localInvalid || casesChanged || item.status !== 'applied' || item.verification?.status !== 'passed'} onClick={() => void task(async () => { await post('activate', { revision: item.applied_revision, generation: item.generation }); window.dispatchEvent(new Event('inkflow-workflows-changed')); })}>明确启用已验证版本</Button>
        {item.status === 'activated' && <Typography>新流程已启用。回退请在设计器选择历史版本；原流程及原运行保持原版本。</Typography>}
        {item.status === 'activated' && item.repair_of && <Button disabled={busy} onClick={() => void task(async () => { const doc = await api<{ document_id: string; revision: string; text: string }>(`/documents/${item.input_refs!.document.document_id}`); const run = await api<{ id: string }>(`/ai/proposals/${item.id}/runs`, { method: 'POST', headers: proposalHeaders(item.id), body: json({ request_id: requestId(), document_id: doc.document_id, revision: doc.revision, text: doc.text, source_snapshot_id: item.input_refs?.source?.snapshot_id ?? null }) }); setNotice(`关联修复运行已创建：${run.id}。请在运行记录中打开原文章审阅。`); })}>以当前已保存工作副本创建关联修复运行</Button>}
        </div>
      </>}
    </section></div><Button hidden={step !== 0 || !item} onClick={() => navigate({ step: '1' })}>继续审阅已有结果</Button></section></>;
}
