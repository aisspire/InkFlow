import { useState } from 'react';
import { Button, MenuItem, TextField } from '@mui/material';
import { runLabels } from './OpenArticles';
import type { RunSummary } from './types';
export type Workspace = { id: string; root: string };
export type FileEntry = { path: string; location: string; modified_at: number; document_id: string | null; draft: boolean };
export type WorkspaceFiles = { id: string; root: string; files: string[]; directories: string[]; entries: FileEntry[] };
export default function WorkspaceExplorer({ data, query, busy, runs, onOpen, onNew }: {
  data: WorkspaceFiles | null; query: string; busy: boolean; runs: Record<string, RunSummary>;
  onOpen: (path: string) => void; onNew: (directory: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [collection, setCollection] = useState(false), [search, setSearch] = useState(''), [sort, setSort] = useState('modified');
  if (!data) return null;
  const files = data.files.filter(p => p.toLowerCase().includes(query.toLowerCase()));
  const paths = [...files, ...data.directories.filter(d => !query || files.some(p => p.startsWith(d + '/'))).map(d => d + '/')];
  function branch(prefix: string): React.ReactNode {
    const names = [...new Set(paths.filter(p => p.startsWith(prefix)).map(p => p.slice(prefix.length).split('/')[0]).filter(Boolean))].sort();
    return names.map(name => {
      const path = prefix + name, directory = paths.some(p => p.startsWith(path + '/'));
      if (directory) return <div key={path} className="article-directory"><div className="workspace-directory-row"><button className="directory-toggle" aria-expanded={!!query || !collapsed[path]} onClick={() => setCollapsed(old => ({ ...old, [path]: !old[path] }))} onKeyDown={e => { if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); setCollapsed(old => ({ ...old, [path]: e.key === 'ArrowLeft' })); } }}>{collapsed[path] ? '▸' : '▾'} {name}</button><Button size="small" disabled={busy} aria-label={`在 ${path} 新建文章`} onClick={() => onNew(path)}>新建</Button></div><div hidden={!query && collapsed[path]}>{branch(path + '/')}</div></div>;
      return <button className="workspace-file" key={path} title={path} disabled={busy} onClick={() => onOpen(path)}>{name}</button>;
    });
  }
  const entries = data.entries.filter(e => e.path.toLowerCase().includes((search || query).toLowerCase())).sort((a,b) => sort === 'name' ? a.path.localeCompare(b.path, 'zh-CN') : b.modified_at - a.modified_at);
  return <section className="workspace-explorer" aria-label="工作区文件"><div className="file-list-heading"><span>工作区文件</span><span>{data.files.length}</span></div><div className="workspace-explorer-actions"><div role="group" aria-label="文件组织视图"><Button aria-pressed={!collection} onClick={() => setCollection(false)}>文件树</Button><Button aria-pressed={collection} onClick={() => setCollection(true)}>文章列表</Button></div><Button disabled={busy} onClick={() => onNew('')}>新建文章</Button></div><nav hidden={collection} aria-label="完整文件树">{branch('')}{!paths.length && <p className="editor-empty">没有匹配文件，可在此新建文章。</p>}</nav>
    <section hidden={!collection} aria-label="工作区文章列表"><div className="collection-controls"><TextField label="搜索文件名或位置" value={search} onChange={e => setSearch(e.target.value)} /><TextField select label="排序" value={sort} onChange={e => setSort(e.target.value)}><MenuItem value="modified">最近修改</MenuItem><MenuItem value="name">文件名</MenuItem></TextField></div><div className="article-collection">{entries.map(entry => <button key={entry.path} disabled={busy} onClick={() => { onOpen(entry.path); }}><strong>{entry.path.split('/').at(-1)}</strong><span>{entry.path}</span><time>{new Date(entry.modified_at * 1000).toLocaleString('zh-CN')}</time><small>{entry.draft ? '草稿未写回' : '磁盘文件'} · {entry.document_id && runs[entry.document_id] ? runLabels[runs[entry.document_id].status] : '未启动工作流'}</small></button>)}{!entries.length && <p>没有匹配文章。</p>}</div></section>
  </section>;
}
