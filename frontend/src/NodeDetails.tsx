import NodeDiagnostics from './NodeDiagnostics';
import { useEffect, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, MenuItem, TextField, Typography } from '@mui/material';
import ArrowBack from '@mui/icons-material/ArrowBack';
import ArrowForward from '@mui/icons-material/ArrowForward';
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined';
import DownloadOutlined from '@mui/icons-material/DownloadOutlined';
import ExpandMore from '@mui/icons-material/ExpandMore';
import SaveOutlined from '@mui/icons-material/SaveOutlined';
import MarkdownPreview from './MarkdownPreview';
import { formatTime } from './api';
import { StatusChip } from './Status';
import { terminal, type ActionHandler, type Pending, type Run, type Workflow } from './types';
import DocumentDiff from './DocumentDiff';

const reviewInputs = new Map<string, { useCandidate: boolean; confirmConflict: boolean; properties: boolean }>();
const articleInputs = new Map<string, import('./types').Article>();
const inputs = new Map<string, { choices: Record<string, { action: string; instruction: string }>; feedback: string }>();
export function HumanAction({ pending, beforeText, busy, onAction, mode = 'simulation', articleText, onReview, canSaveProperties = true }: { pending: Pending; beforeText: string; busy: boolean; onAction: ActionHandler; mode?: 'article' | 'task' | 'simulation'; articleText?: string; onReview?: () => void; canSaveProperties?: boolean }) {
  const [choices, setChoices] = useState<Record<string, { action: string; instruction: string }>>(() => inputs.get(pending.id)?.choices ?? {});
  const [feedback, setFeedback] = useState(() => inputs.get(pending.id)?.feedback ?? '');
  useEffect(() => { inputs.set(pending.id, { choices, feedback }); }, [pending.id, choices, feedback]);
  const [error, setError] = useState('');
  const [properties, setProperties] = useState(() => reviewInputs.get(pending.id)?.properties ?? false);
  const [useCandidate, setUseCandidate] = useState(() => reviewInputs.get(pending.id)?.useCandidate ?? false);
  const [publishing, setPublishing] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [article, setArticle] = useState(() => articleInputs.get(pending.id) ?? pending.article);
  const [confirmConflict, setConfirmConflict] = useState(() => reviewInputs.get(pending.id)?.confirmConflict ?? false);
  useEffect(() => { reviewInputs.set(pending.id, { useCandidate, confirmConflict, properties }); }, [pending.id, useCandidate, confirmConflict, properties]);
  useEffect(() => { if (article) articleInputs.set(pending.id, article); }, [pending.id, article]);
  async function send(action: string, payload: Record<string, unknown> = {}) {
    setError('');
    try { await onAction(action, payload); } catch (err) { setError((err as Error).message); }
  }
  const revision = pending.revision;
  const target = pending.publish_preview;
  const body = articleText ?? article?.body ?? '';
  if (mode === 'task' && ['article_metadata', 'suggestion_review', 'diff_review', 'final_review'].includes(pending.kind)) return <div className="form-stack"><Alert severity="info">此待办需要核对文章工作副本、运行候选和当前版本。</Alert>{onReview ? <Button variant="contained" onClick={onReview}>前往文章审阅</Button> : <Typography variant="body2">此历史运行未关联文章，可查看记录；不能确认文章候选。</Typography>}</div>;
  return <div className="human-action form-stack">
    <div className="section-eyebrow"><span className="waiting-dot" />需要你的确认</div>
    {error && <Alert severity="error">{error}</Alert>}
    {pending.simulated && <Alert severity="info">模拟待办：提交动作不会修改文章或写入输出文件。</Alert>}
    {pending.kind === 'article_metadata' && article && <>
      <Typography variant="subtitle2">文章属性</Typography>
      {(['title', 'slug', 'date', 'description', 'authors', 'tags'] as const).map(key => <TextField key={key} label={({ title: '标题', slug: 'slug', date: '日期 YYYY-MM-DD', description: '摘要', authors: '作者（逗号分隔）', tags: '标签（逗号分隔）' })[key]} value={Array.isArray(article[key]) ? article[key].join(', ') : article[key]} disabled={busy} onChange={e => setArticle({ ...article, [key]: ['authors', 'tags'].includes(key) ? e.target.value.split(',').map(x => x.trim()).filter(Boolean) : e.target.value })} />)}
      <FormControlLabel control={<Checkbox checked={article.draft} disabled={busy} onChange={e => setArticle({ ...article, draft: e.target.checked })} />} label="博客草稿标记" />
      {mode === 'simulation' ? <TextField label="模拟样例正文" value={article.body} multiline minRows={4} disabled={busy} onChange={e => setArticle({ ...article, body: e.target.value })} /> : <>
        <Typography variant="body2">正文在主编辑区修改；下方比较运行候选与当前工作副本。</Typography>
        <DocumentDiff before={body} after={pending.article?.body ?? ''} beforeLabel="当前工作副本" afterLabel="运行候选" />
        <FormControlLabel label="采用运行候选正文（当前内容保留历史）" control={<Checkbox checked={useCandidate} onChange={e => setUseCandidate(e.target.checked)} />} />
      </>}
      <FormControlLabel control={<Checkbox checked={confirmConflict} disabled={busy} onChange={e => setConfirmConflict(e.target.checked)} />} label="若工作副本自启动后有修改，保留历史并明确采用这份审阅稿" />
      <Button variant="contained" disabled={busy} onClick={() => void send('approve_article', { article: { ...article, body: mode === 'simulation' ? article.body : useCandidate ? pending.article?.body ?? '' : body }, confirm_conflict: confirmConflict })}>确认候选与属性，采用到工作副本</Button>
    </>}
    {pending.kind === 'local_output' && <>
      <Typography>将写入此目标：</Typography><pre className="path-preview">{target?.path}</pre>
      <Typography variant="caption">成功仅表示本地文件已写入。确认后将再次核对目标指纹。</Typography>
      {target?.exists && <FormControlLabel control={<Checkbox checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />} label="我已核对，允许覆盖现有文件" />}
      {target?.exists && pending.overwrite_policy === 'never' && <Alert severity="error">此配置拒绝覆盖；请停止后更换目标。</Alert>}
      <Accordion><AccordionSummary expandIcon={<ExpandMore />}>本次输出文档</AccordionSummary><AccordionDetails><pre>{pending.document}</pre></AccordionDetails></Accordion>
      <Button variant="contained" disabled={busy || !!target?.exists && (!overwrite || pending.overwrite_policy === 'never')} onClick={() => void send('confirm_output', { target, overwrite })}>{pending.simulated ? '确认模拟输出' : '确认写入本地文件'}</Button>
    </>}
    {pending.simulated && <Button color="error" disabled={busy} onClick={() => void send('stop')}>停止此模拟</Button>}
    {pending.kind === 'suggestion_review' && <Button disabled={busy} onClick={() => void send('finish_check')}>结束当前检查</Button>}
    {pending.kind === 'redaction_review' && <>
      {!pending.llm_enabled && <Alert severity="warning">模型未启用，本次没有自动风险分析。请人工检查原文。</Alert>}
      {!pending.findings?.length && <Typography variant="body2">{pending.redaction_only ? '本轮检查未发现风险。确认后结束，不生成文章或发布。' : '没有待处理的敏感项。确认后将进入文章生成。'}</Typography>}
      {pending.findings?.map((finding, index) => {
        const choice = choices[finding.id] ?? { action: 'apply', instruction: '' };
        return <div className="finding" key={finding.id}>
          <div className="finding-heading"><Typography fontWeight={600} variant="body2">{index + 1}. {finding.issue}</Typography><Chip size="small" color={finding.risk === 'high' ? 'error' : finding.risk === 'medium' ? 'warning' : 'default'} label={{ high: '高风险', medium: '中风险', low: '低风险' }[finding.risk]} /></div>
          <Typography variant="caption" color="text.secondary">第 {finding.line_start}–{finding.line_end} 行</Typography>
          <Typography variant="body2">{finding.reason}</Typography>
          <blockquote>{finding.original_excerpt || '无原文片段'}</blockquote>
          <Typography variant="body2" color="text.secondary">建议：{finding.suggestion}</Typography>
          <TextField select label={`第 ${index + 1} 项处理方式`} value={choice.action} onChange={e => setChoices({ ...choices, [finding.id]: { ...choice, action: e.target.value } })}>
            <MenuItem value="apply">采用建议</MenuItem><MenuItem value="ignore">忽略此项</MenuItem><MenuItem value="custom">自定义修改</MenuItem>
          </TextField>
          {choice.action === 'custom' && <TextField label={`第 ${index + 1} 项修改方案`} multiline minRows={2} value={choice.instruction} onChange={e => setChoices({ ...choices, [finding.id]: { ...choice, instruction: e.target.value } })} />}
        </div>;
      })}
      <Button variant="contained" disabled={busy} endIcon={<ArrowForward />} onClick={() => void send('review', { decisions: pending.findings?.map(item => ({ finding_id: item.id, ...(choices[item.id] ?? { action: 'apply', instruction: '' }) })) ?? [] })}>{pending.redaction_only ? (pending.findings?.length ? '确认审核（全部忽略则结束）' : '确认结束本轮脱敏') : '确认审核并继续'}</Button>
    </>}
    {pending.kind === 'diff_review' && <>
      <Typography variant="body2">核对下面的实际改动。接受后会重新检查敏感内容。</Typography>
      <DocumentDiff before={pending.before_text ?? beforeText} after={pending.candidate_text ?? ''} beforeLabel="改写前" afterLabel="候选脱敏稿" />
      <TextField multiline minRows={2} label="重试建议（可选）" value={feedback} onChange={e => setFeedback(e.target.value)} />
      <Button variant="contained" disabled={busy} onClick={() => void send('accept')}>接受改动并复查</Button>
      <Button variant="outlined" disabled={busy} onClick={() => void send('retry', { feedback })}>按建议重新改写</Button>
    </>}
    {pending.kind === 'article_question' && <>
      <Chip size="small" label={`第 ${pending.question_number} / 3 次追问`} sx={{ alignSelf: 'flex-start' }} />
      <Typography fontWeight={600}>{pending.question}</Typography>
      <TextField autoFocus label="你的回答" multiline minRows={4} value={feedback} onChange={e => setFeedback(e.target.value)} />
      <Button variant="contained" disabled={busy || !feedback.trim()} onClick={() => void send('answer', { answer: feedback })}>提交回答</Button>
    </>}
    {pending.kind === 'final_review' && <>
      {pending.degraded && <Alert severity="warning">这是占位文章，尚未达到正式稿质量。请检查并编辑后再确认。</Alert>}
      <Typography fontWeight={600}>{pending.article?.title}</Typography>
      <Typography variant="body2" color="text.secondary">已确认审阅版本 {revision}。工作副本修改需另行提交；输出以此版本为准。</Typography>
      <Typography variant="body2">运行候选请先对比并采用到主编辑区。正文修改后需“提交编辑稿到工作流”；确认输出使用下方已确认版本。</Typography>
      <Button onClick={() => setProperties(v => !v)} disabled={busy}>文章属性</Button>
      {properties && article && <section className="form-stack" aria-label="文章属性">{(['title', 'slug', 'description', 'date'] as const).map(key => <TextField key={key} label={({ title: '标题', slug: 'slug', description: '摘要', date: '日期' })[key]} value={article[key]} onChange={e => setArticle({ ...article, [key]: e.target.value })} />)}{(['tags', 'authors'] as const).map(key => <TextField key={key} label={key === 'tags' ? '标签（逗号分隔）' : '作者（逗号分隔）'} value={article[key].join(', ')} onChange={e => setArticle({ ...article, [key]: e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) })} />)}<FormControlLabel label="博客草稿标记" control={<Checkbox checked={article.draft} onChange={e => setArticle({ ...article, draft: e.target.checked })} />} />{!canSaveProperties && <Alert severity="info">当前工作副本尚未提交，请先提交主编辑稿，再核对这份版本的属性。</Alert>}<Button disabled={busy || !canSaveProperties} onClick={() => void send('save', { revision, article })}>提交属性为新审阅版本</Button></section>}
      <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>已保存的文档</AccordionSummary><AccordionDetails><pre className="data-preview">{pending.document}</pre></AccordionDetails></Accordion>
      <div className="publish-target"><Typography variant="caption" fontWeight={600}>博客目标</Typography><p>{target?.path ?? target?.error}</p>{target?.path && <Chip size="small" color={target.exists ? 'warning' : 'default'} label={target.exists ? '已存在 · 需要确认覆盖' : '将创建新文件'} />}</div>
      <Button variant="contained" startIcon={<SaveOutlined />} disabled={busy} onClick={() => void send('finish', { revision })}>保存审阅稿并结束</Button>
      <Button variant="outlined" disabled={busy || pending.no_publish || !target?.path} onClick={() => setPublishing(true)}>确认写入博客</Button>
      {pending.no_publish && <Typography variant="caption" color="text.secondary">本次任务已禁用博客写入。</Typography>}
      <Divider />
      <TextField label="退回修改建议（可选）" multiline minRows={2} value={feedback} onChange={e => setFeedback(e.target.value)} />
      <div className="button-row"><Button size="small" startIcon={<ArrowBack />} disabled={busy} onClick={() => void send('regenerate', { revision, feedback })}>退回生成</Button><Button size="small" disabled={busy} onClick={() => void send('redact_again', { revision, feedback })}>退回脱敏</Button></div>
      <Typography variant="caption" color="text.secondary">退回后，当前版本的最终确认会失效。</Typography>
      <Dialog open={publishing} onClose={busy ? undefined : () => setPublishing(false)} aria-labelledby="publish-title">
        <DialogTitle id="publish-title">确认写入博客</DialogTitle><DialogContent><div className="form-stack"><Typography>将写入已保存的版本 {revision}：</Typography><pre className="path-preview">{target?.path}</pre><Typography variant="body2">仅写入 index.mdx。此操作不会执行构建或 Git 命令。</Typography>{target?.exists && <FormControlLabel control={<Checkbox checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />} label="我已核对目标，允许覆盖现有文件" />}{error && <Alert severity="error">{error}</Alert>}</div></DialogContent>
        <DialogActions><Button onClick={() => setPublishing(false)} disabled={busy}>取消</Button><Button variant="contained" disabled={busy || (target?.exists && !overwrite)} onClick={() => void send('publish', { revision, target, overwrite })}>写入博客</Button></DialogActions>
      </Dialog>
    </>}
  </div>;
}

export default function NodeDetails({ workflow, run, selected, busy, onAction, onSelect, mode = 'article', articleText, onReview, canSaveProperties = true }: {
  workflow: Workflow; run: Run | null; selected: string | null; busy: boolean; onAction: ActionHandler; onSelect: (id: string) => void; mode?: 'article' | 'task'; articleText?: string; onReview?: () => void; canSaveProperties?: boolean;
}) {
  const [recoveryError, setRecoveryError] = useState('');
  async function recover(action: string) { setRecoveryError(''); try { await onAction(action); } catch (e) { setRecoveryError((e as Error).message); } }
  const node = workflow.nodes.find(item => item.id === selected);
  const executions = run?.executions.filter(item => item.node === selected) ?? [];
  const last = executions.at(-1);
  const pending = run?.pending;
  return <aside className="node-details" aria-label="节点详情">
    <div className="details-header"><Typography variant="overline" color="text.secondary">节点详情</Typography><Typography variant="h6">{node?.label ?? '查看流程中的每一步'}</Typography>{node && <Typography color="text.secondary" variant="body2">{node.description}</Typography>}</div>
    <div className="details-scroll">
      {pending && pending.node !== selected && <Alert severity="warning" action={<Button size="small" onClick={() => onSelect(pending.node)}>处理</Button>}>有待处理的人工操作</Alert>}
      {!node && <div className="detail-empty"><DescriptionOutlined /><Typography variant="body2">点击流程中的节点，查看输入、输出与执行记录。</Typography></div>}
      {node && <div className="detail-facts"><StatusChip status={last?.status ?? (run && terminal(run.status) ? 'skipped' : 'idle')} /><Typography variant="caption" color="text.secondary">已执行 {executions.length} 次</Typography></div>}
      {node && (run?.version_package?.schema_version === 2 || ['full_check', 'redaction_plan', 'apply_redaction', 'article_generation'].includes(node.id)) && <Button onClick={() => window.dispatchEvent(new CustomEvent('inkflow-open-studio', { detail: { workflowId: workflow.id, nodeId: node.id, versionId: run?.workflow_version, package: run?.version_package } }))}>{run?.version_package ? '查看运行提示词与版本（只读）' : '打开当前流程提示词'}</Button>}
      {run && <Typography variant="caption">{run.workflow_version ? '固定流程版本 ' + run.workflow_version.slice(0, 12) : '旧版，提示词版本未知'}</Typography>}
      {run?.repair_of && <Typography variant="caption" className="studio-identifier">关联修复：原运行 {run.repair_of.run_id.slice(0, 12)} · 节点 {run.repair_of.node_id} · 失败尝试 {run.repair_of.attempt_id.slice(0, 12)}。本次重新计算输入，未复用上游产物。</Typography>}
      {run && node && <Accordion><AccordionSummary expandIcon={<ExpandMore />}>技术详情与诊断</AccordionSummary><AccordionDetails><NodeDiagnostics base={`/runs/${run.id}`} nodeId={node.id} documentId={run.editor_source?.document_id} refresh={run.event_cursor} /></AccordionDetails></Accordion>}
      {recoveryError && <Alert severity="error">{recoveryError}</Alert>}
      {run?.status === 'recovering' && <Alert severity="info">正在恢复固定版本与输入，请稍候。</Alert>}
      {run && ['verification_required', 'recovery_blocked'].includes(run.status) && <Alert severity="warning">
        <p>{run.recovery_message}</p>
        {run.effects?.filter(e => e.state !== 'succeeded').map(e => <pre className="path-preview" key={e.id}>{e.path}</pre>)}
        {run.status === 'verification_required' && <Button disabled={busy} onClick={() => void recover('reconcile')}>重新核验原目标</Button>}
        <Button color="error" disabled={busy} onClick={() => void recover('stop')}>停止此运行并保留文件</Button>
      </Alert>}
      {run?.status === 'waiting' && pending?.node === selected && <HumanAction key={pending.id} pending={pending} beforeText={run?.state.checked_text ?? run?.state.redacted_text ?? run?.state.clean_text ?? run?.state.raw_text ?? ''} busy={busy} onAction={onAction} mode={mode} articleText={articleText} onReview={onReview} canSaveProperties={canSaveProperties} />}
      {node && !executions.length && <Typography variant="body2" color="text.secondary" sx={{ my: 2 }}>{run && terminal(run.status) ? '本次任务没有执行此节点。' : '节点尚未执行，运行后会显示实际输入输出。'}</Typography>}
      {executions.length > 0 && <div className="execution-history"><Typography variant="subtitle2">执行记录</Typography>{[...executions].reverse().map(execution => <Accordion key={execution.id} disableGutters>
        <AccordionSummary expandIcon={<ExpandMore />}><div className="execution-summary"><span>第 {execution.iteration} 次执行</span><StatusChip status={execution.status} /></div></AccordionSummary>
        <AccordionDetails><Typography variant="caption" color="text.secondary">{formatTime(execution.started_at)}</Typography><Typography variant="subtitle2" sx={{ mt: 2 }}>输入</Typography><pre className="data-preview">{JSON.stringify(execution.input, null, 2)}</pre><Typography variant="subtitle2">输出</Typography><pre className="data-preview">{JSON.stringify(execution.output, null, 2)}</pre></AccordionDetails>
      </Accordion>)}</div>}
      {run?.artifacts.length ? <div className="artifact-list"><Typography variant="subtitle2">任务产物</Typography>{run.artifacts.map(artifact => <Button key={artifact.id} component="a" href={`/api/runs/${run.id}/artifacts/${artifact.id}?download=true${run.editor_source?.document_id ? `&document_id=${run.editor_source.document_id}` : ''}`} startIcon={<DownloadOutlined />} size="small">{artifact.label}</Button>)}</div> : null}
      {run && terminal(run.status) && run.state.article_data?.body && selected === 'final_review' && <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>最终文章预览</AccordionSummary><AccordionDetails><MarkdownPreview text={run.state.article_data.body} /></AccordionDetails></Accordion>}
    </div>
  </aside>;
}
