import { useState } from 'react';
import Close from '@mui/icons-material/Close';
import { IconButton } from '@mui/material';
import { terminal, type RunSummary } from './types';
const labels: Record<string, string> = { running: '运行中', waiting: '等待确认', stopping: '停止中', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断' };
export default function OpenArticles({ paths, current, runs, busy, query, onOpen, onClose }: {
  paths: string[]; query: string; current?: string; runs: Record<string, RunSummary>; busy: boolean;
  onOpen: (path: string) => void; onClose: (path: string) => void;
}) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  function branch(prefix: string) {
    const children = [...new Set(paths.filter(p => p.startsWith(prefix)).map(p => p.slice(prefix.length).split('/')[0]))].sort();
    return children.map(name => {
      const path = prefix + name;
      if (!paths.includes(path)) return <div key={path} className="article-directory"><button className="directory-toggle" aria-expanded={!!query || !collapsed[path]} onClick={() => setCollapsed(old => ({ ...old, [path]: !old[path] }))} onKeyDown={e => {
        if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); setCollapsed(old => ({ ...old, [path]: e.key === 'ArrowLeft' })); }
      }}>{collapsed[path] ? '▸' : '▾'} {name}</button><div hidden={!query && collapsed[path]}>{branch(path + '/')}</div></div>;
      const run = runs[path];
      const state = !run ? '未启动' : terminal(run.status) ? labels[run.status] : `${run.workflow_name} · ${run.status === 'waiting' && run.workflow_id === 'full-check' ? `待处理 ${run.pending_count ?? 0}` : labels[run.status]}`;
      return <div key={path} className="opened-article"><button disabled={busy} title={path} aria-current={current === path ? 'page' : undefined} onClick={() => onOpen(path)}><strong>{name}</strong><small role="status">{state}</small></button><IconButton size="small" disabled={busy} aria-label={`关闭 ${name}`} onClick={() => onClose(path)}><Close fontSize="small" /></IconButton></div>;
    });
  }
  return <nav className="file-list opened-articles" aria-label="已打开文章">{branch('')}{!paths.length && <p className="editor-empty">打开文章后在这里切换。</p>}</nav>;
}
