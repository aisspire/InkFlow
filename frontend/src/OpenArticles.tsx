import Close from '@mui/icons-material/Close';
import ChevronRight from '@mui/icons-material/ChevronRight';
import { useEffect, useState } from 'react';
import { IconButton, Tooltip } from '@mui/material';
import type { RunSummary } from './types';
export type DocumentSummary = { document_id: string; path: string; location: string | null; revision: string; updated_at: string; draft: boolean; file_error?: string | null };
export const runLabels: Record<string, string> = { recovering: '恢复中', verification_required: '等待核验', recovery_blocked: '恢复受阻', running: '运行中', waiting: '等待确认', stopping: '停止中', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断' };

export function articleName(doc: DocumentSummary) {
  return doc.location ? doc.path.split(/[\\/]/).at(-1)! : '未命名文章 · ' + doc.document_id.slice(0, 6);
}

export function ArticleFileButton({ doc, run, current, busy, onOpen }: {
  doc: DocumentSummary; run?: RunSummary; current?: boolean; busy: boolean; onOpen: (id: string) => void;
}) {
  return <Tooltip describeChild disableInteractive placement="right" title={<div className="article-file-details"><strong>{doc.location || '未命名文章 · 尚未选择位置'}</strong><span>{doc.draft ? '草稿未写回' : '已保存到文件'}</span>{doc.file_error && <span>原文件失联 · {doc.file_error}</span>}{run && <span>工作流 · {runLabels[run.status] || run.status}</span>}</div>}>
    <button className="article-file-button" data-document-id={doc.document_id} disabled={busy} aria-current={current ? 'page' : undefined} onClick={() => onOpen(doc.document_id)}><strong>{articleName(doc)}</strong></button>
  </Tooltip>;
}

type Branch = { path: string; name: string; children: Map<string, Branch>; document?: DocumentSummary };
const normalize = (path: string) => path.replace(/\\/g, '/');
function paths(documents: DocumentSummary[]) {
  const roots = new Map<string, Branch>();
  for (const doc of documents) {
    const path = normalize(doc.location!);
    const root = path.startsWith('//') ? '//' : path.startsWith('/') ? '/' : path.slice(0, path.indexOf('/') + 1);
    const parts = path.slice(root.length).split('/').filter(Boolean);
    if (!roots.has(root)) roots.set(root, { path: root, name: root, children: new Map() });
    let node = roots.get(root)!;
    for (const part of parts) {
      const key = node.path.replace(/\/$/, '') + '/' + part;
      if (!node.children.has(part)) node.children.set(part, { path: key, name: part, children: new Map() });
      node = node.children.get(part)!;
    }
    node.document = doc;
  }
  return roots;
}

export default function OpenArticles({ documents, current, query, runs, busy, onOpen, onRemove }: {
  documents: DocumentSummary[]; current?: string; query: string; runs: Record<string, RunSummary>; busy: boolean;
  onOpen: (id: string) => void; onRemove: (id: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const currentPath = normalize(documents.find(d => d.document_id === current)?.location || '');
  useEffect(() => {
    setCollapsed(old => Object.fromEntries(Object.entries(old).filter(([path]) => !currentPath.startsWith(path.replace(/\/$/, '') + '/'))));
  }, [currentPath]);
  const visible = documents.filter(d => d.path.toLowerCase().includes(query.trim().toLowerCase()));
  function article(doc: DocumentSummary) {
    const name = articleName(doc);
    return <div key={doc.document_id} className="opened-article"><ArticleFileButton doc={doc} run={runs[doc.document_id]} current={current === doc.document_id} busy={busy} onOpen={onOpen} /><Tooltip title="从侧栏移除，保留文件、草稿和历史"><span><IconButton size="small" disabled={busy} aria-label={`从侧栏移除 ${name}`} onClick={() => onRemove(doc.document_id)}><Close fontSize="small" /></IconButton></span></Tooltip></div>;
  }
  function branch(nodes: Map<string, Branch>): React.ReactNode {
    return [...nodes.values()].sort((a, b) => Number(!!a.document) - Number(!!b.document) || a.name.localeCompare(b.name, 'zh-CN')).map(original => {
      if (original.document) return article(original.document);
      let node = original, name = node.name;
      while (node.children.size === 1) {
        const child = [...node.children.values()][0];
        if (child.document) break;
        name = name.replace(/\/$/, '') + '/' + child.name; node = child;
      }
      const path = node.path, expanded = !!query.trim() || !collapsed[path];
      if (original.name.endsWith('/') && name.length > 48) {
        const root = original.name === '//' ? '//' + path.split('/').filter(Boolean)[0] + '/' : original.name;
        name = root + '…/' + path.split('/').at(-1);
      }
      return <div key={path} className="article-directory"><button className="directory-toggle" title={path} aria-expanded={expanded} onClick={() => setCollapsed(old => ({ ...old, [path]: !old[path] }))} onKeyDown={event => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setCollapsed(old => ({ ...old, [path]: event.key === 'ArrowLeft' })); }
      }}><ChevronRight className="disclosure-chevron" /><span className="directory-name">{name}</span></button><div hidden={!expanded}>{branch(node.children)}</div></div>;
    });
  }
  const drafts = visible.filter(d => !d.location);
  return <nav className="file-list article-path-tree" aria-label="文章路径树">
    {!!drafts.length && <section aria-label="未命名草稿"><div className="draft-list-heading">未命名</div>{drafts.map(article)}</section>}
    {branch(paths(visible.filter(d => !!d.location)))}
    {!visible.length && <p className="editor-empty">{query.trim() ? '没有匹配文章。' : '打开或新建文章后在这里保留。'}</p>}
  </nav>;
}
