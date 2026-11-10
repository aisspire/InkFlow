import { useEffect, useState } from 'react';
import { Accordion, AccordionDetails, AccordionSummary, Alert, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, MenuItem, TextField, Typography } from '@mui/material';
import ArrowBack from '@mui/icons-material/ArrowBack';
import ArrowForward from '@mui/icons-material/ArrowForward';
import DescriptionOutlined from '@mui/icons-material/DescriptionOutlined';
import DownloadOutlined from '@mui/icons-material/DownloadOutlined';
import EditOutlined from '@mui/icons-material/EditOutlined';
import ExpandMore from '@mui/icons-material/ExpandMore';
import SaveOutlined from '@mui/icons-material/SaveOutlined';
import ArticleEditor, { MarkdownPreview } from './ArticleEditor';
import { formatTime } from './api';
import { StatusChip } from './Status';
import { terminal, type ActionHandler, type Pending, type Run, type Workflow } from './types';
import DocumentDiff from './DocumentDiff';

const inputs = new Map<string, { choices: Record<string, { action: string; instruction: string }>; feedback: string }>();
function HumanAction({ pending, beforeText, busy, onAction }: { pending: Pending; beforeText: string; busy: boolean; onAction: ActionHandler }) {
  const [choices, setChoices] = useState<Record<string, { action: string; instruction: string }>>(() => inputs.get(pending.id)?.choices ?? {});
  const [feedback, setFeedback] = useState(() => inputs.get(pending.id)?.feedback ?? '');
  useEffect(() => { inputs.set(pending.id, { choices, feedback }); }, [pending.id, choices, feedback]);
  const [error, setError] = useState('');
  const [editor, setEditor] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  async function send(action: string, payload: Record<string, unknown> = {}) {
    setError('');
    try { await onAction(action, payload); } catch (err) { setError((err as Error).message); }
  }
  const revision = pending.revision;
  const target = pending.publish_preview;
  return <div className="human-action form-stack">
    <div className="section-eyebrow"><span className="waiting-dot" />需要你的确认</div>
    {error && <Alert severity="error">{error}</Alert>}
    {pending.kind === 'suggestion_review' && <Button disabled={busy} onClick={() => void send('finish_check')}>结束当前检查</Button>}
    {pending.kind === 'redaction_review' && <>
      {!pending.llm_enabled && <Alert severity="warning">模型未启用，本次没有自动风险分析。请人工检查原文。</Alert>}
      {!pending.findings?.length && <Typography variant="body2">没有待处理的敏感项。确认后将进入文章生成。</Typography>}
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
      <Button variant="contained" disabled={busy} endIcon={<ArrowForward />} onClick={() => void send('review', { decisions: pending.findings?.map(item => ({ finding_id: item.id, ...(choices[item.id] ?? { action: 'apply', instruction: '' }) })) ?? [] })}>确认审核并继续</Button>
    </>}
    {pending.kind === 'diff_review' && <>
      <Typography variant="body2">核对下面的实际改动。接受后会重新检查敏感内容。</Typography>
      <DocumentDiff before={beforeText} after={pending.candidate_text ?? ''} beforeLabel="改写前" afterLabel="候选脱敏稿" />
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
      <Typography variant="body2" color="text.secondary">版本 {revision} 已保存。正文与博客输出均以此版本为准。</Typography>
      <Button variant="outlined" startIcon={<EditOutlined />} onClick={() => setEditor(true)} disabled={busy}>编辑文章与预览</Button>
      <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>已保存的文档</AccordionSummary><AccordionDetails><pre className="data-preview">{pending.document}</pre></AccordionDetails></Accordion>
      <div className="publish-target"><Typography variant="caption" fontWeight={600}>博客目标</Typography><p>{target?.path ?? target?.error}</p>{target?.path && <Chip size="small" color={target.exists ? 'warning' : 'default'} label={target.exists ? '已存在 · 需要确认覆盖' : '将创建新文件'} />}</div>
      <Button variant="contained" startIcon={<SaveOutlined />} disabled={busy} onClick={() => void send('finish', { revision })}>保存审阅稿并结束</Button>
      <Button variant="outlined" disabled={busy || pending.no_publish || !target?.path} onClick={() => setPublishing(true)}>确认写入博客</Button>
      {pending.no_publish && <Typography variant="caption" color="text.secondary">本次任务已禁用博客写入。</Typography>}
      <Divider />
      <TextField label="退回修改建议（可选）" multiline minRows={2} value={feedback} onChange={e => setFeedback(e.target.value)} />
      <div className="button-row"><Button size="small" startIcon={<ArrowBack />} disabled={busy} onClick={() => void send('regenerate', { revision, feedback })}>退回生成</Button><Button size="small" disabled={busy} onClick={() => void send('redact_again', { revision, feedback })}>退回脱敏</Button></div>
      <Typography variant="caption" color="text.secondary">退回后，当前版本的最终确认会失效。</Typography>
      {editor && <ArticleEditor open pending={pending} busy={busy} onClose={() => setEditor(false)} onAction={onAction} />}
      <Dialog open={publishing} onClose={busy ? undefined : () => setPublishing(false)} aria-labelledby="publish-title">
        <DialogTitle id="publish-title">确认写入博客</DialogTitle><DialogContent><div className="form-stack"><Typography>将写入已保存的版本 {revision}：</Typography><pre className="path-preview">{target?.path}</pre><Typography variant="body2">仅写入 index.mdx。此操作不会执行构建或 Git 命令。</Typography>{target?.exists && <FormControlLabel control={<Checkbox checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />} label="我已核对目标，允许覆盖现有文件" />}{error && <Alert severity="error">{error}</Alert>}</div></DialogContent>
        <DialogActions><Button onClick={() => setPublishing(false)} disabled={busy}>取消</Button><Button variant="contained" disabled={busy || (target?.exists && !overwrite)} onClick={() => void send('publish', { revision, target, overwrite })}>写入博客</Button></DialogActions>
      </Dialog>
    </>}
  </div>;
}

export default function NodeDetails({ workflow, run, selected, busy, onAction, onSelect }: {
  workflow: Workflow; run: Run | null; selected: string | null; busy: boolean; onAction: ActionHandler; onSelect: (id: string) => void;
}) {
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
      {pending?.node === selected && <HumanAction key={pending.id} pending={pending} beforeText={run?.state.checked_text ?? run?.state.redacted_text ?? run?.state.clean_text ?? run?.state.raw_text ?? ''} busy={busy} onAction={onAction} />}
      {node && !executions.length && <Typography variant="body2" color="text.secondary" sx={{ my: 2 }}>{run && terminal(run.status) ? '本次任务没有执行此节点。' : '节点尚未执行，运行后会显示实际输入输出。'}</Typography>}
      {executions.length > 0 && <div className="execution-history"><Typography variant="subtitle2">执行记录</Typography>{[...executions].reverse().map(execution => <Accordion key={execution.id} disableGutters>
        <AccordionSummary expandIcon={<ExpandMore />}><div className="execution-summary"><span>第 {execution.iteration} 次执行</span><StatusChip status={execution.status} /></div></AccordionSummary>
        <AccordionDetails><Typography variant="caption" color="text.secondary">{formatTime(execution.started_at)}</Typography><Typography variant="subtitle2" sx={{ mt: 2 }}>输入</Typography><pre className="data-preview">{JSON.stringify(execution.input, null, 2)}</pre><Typography variant="subtitle2">输出</Typography><pre className="data-preview">{JSON.stringify(execution.output, null, 2)}</pre></AccordionDetails>
      </Accordion>)}</div>}
      {run?.artifacts.length ? <div className="artifact-list"><Typography variant="subtitle2">任务产物</Typography>{run.artifacts.map(artifact => <Button key={artifact.id} component="a" href={`/api/runs/${run.id}/artifacts/${artifact.id}?download=true`} startIcon={<DownloadOutlined />} size="small">{artifact.label}</Button>)}</div> : null}
      {run && terminal(run.status) && run.state.article_data?.body && selected === 'final_review' && <Accordion disableGutters><AccordionSummary expandIcon={<ExpandMore />}>最终文章预览</AccordionSummary><AccordionDetails><MarkdownPreview text={run.state.article_data.body} /></AccordionDetails></Accordion>}
    </div>
  </aside>;
}
