import { useState } from 'react';
import { Alert, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, TextField } from '@mui/material';
import MarkdownPreview from './MarkdownPreview';
import type { ActionHandler, Article, Pending } from './types';

export { default as MarkdownPreview } from './MarkdownPreview';
// 保留既有任务 final_review 的结构化文章接口；原始本地文章由 SourceEditor 独立处理。
export default function ArticleEditor({ open, pending, busy, onClose, onAction }: {
  open: boolean; pending: Pending; busy: boolean; onClose: () => void; onAction: ActionHandler;
}) {
  const [article, setArticle] = useState<Article>(pending.article!);
  const [preview, setPreview] = useState(false), [error, setError] = useState('');
  const dirty = JSON.stringify(article) !== JSON.stringify(pending.article);
  const close = () => { if (!busy && (!dirty || window.confirm('放弃尚未提交的修改？'))) onClose(); };
  return <Dialog open={open} onClose={close} fullWidth maxWidth="lg"><DialogTitle>编辑文章 · 版本 {pending.revision}</DialogTitle><DialogContent><div className="form-stack">
    {error && <Alert severity="error">{error}</Alert>}
    {(['title','slug','description','date'] as const).map(key => <TextField key={key} label={{title:'标题',slug:'slug',description:'摘要',date:'日期'}[key]} value={article[key]} onChange={e => setArticle({ ...article, [key]: e.target.value })} />)}
    {(['tags','authors'] as const).map(key => <TextField key={key} label={key === 'tags' ? '标签（逗号分隔）' : '作者（逗号分隔）'} value={article[key].join(', ')} onChange={e => setArticle({ ...article, [key]: e.target.value.split(/[,，]/).map(x => x.trim()).filter(Boolean) })} />)}
    <FormControlLabel label="草稿" control={<Checkbox checked={article.draft} onChange={e => setArticle({ ...article, draft: e.target.checked })} />} />
    <Button onClick={() => setPreview(!preview)}>{preview ? '返回编辑' : '预览正文'}</Button>
    {preview ? <MarkdownPreview text={article.body} /> : <TextField label="Markdown 正文" multiline minRows={16} value={article.body} onChange={e => setArticle({ ...article, body: e.target.value })} />}
  </div></DialogContent><DialogActions><Button disabled={busy} onClick={close}>取消</Button><Button variant="contained" disabled={busy} onClick={() => { setError(''); void onAction('save', { revision: pending.revision, article }).then(onClose).catch(e => setError((e as Error).message)); }}>提交文章版本</Button></DialogActions></Dialog>;
}
