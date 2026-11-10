import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react';
import { Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Drawer, IconButton, LinearProgress, Tooltip, TextField, useMediaQuery } from '@mui/material';
import ArticleOutlined from '@mui/icons-material/ArticleOutlined';
import Check from '@mui/icons-material/Check';
import ChevronRight from '@mui/icons-material/ChevronRight';
import Close from '@mui/icons-material/Close';
import FolderOutlined from '@mui/icons-material/FolderOutlined';
import FolderOpenOutlined from '@mui/icons-material/FolderOpenOutlined';
import HistoryOutlined from '@mui/icons-material/HistoryOutlined';
import HubOutlined from '@mui/icons-material/HubOutlined';
import Menu from '@mui/icons-material/Menu';
import Redo from '@mui/icons-material/Redo';
import Refresh from '@mui/icons-material/Refresh';
import Search from '@mui/icons-material/Search';
import SettingsOutlined from '@mui/icons-material/SettingsOutlined';
import Undo from '@mui/icons-material/Undo';
import { api, chooseLocalPath, formatTime, requestId, setWorkspace } from './api';
import ArticleEditor, { type EditorHandle } from './SourceEditor';
import MarkdownPreview from './MarkdownPreview';
import SettingsPage from './SettingsPage';
import WorkflowPanel, { WorkflowControls, type AuxiliaryView } from './App';
import NewRunDialog from './NewRunDialog';
import { useWorkflow } from './useWorkflow';
import WorkflowDocumentBridge from './WorkflowDocumentBridge';
import { terminal, type Run, type WorkflowApplyRequest, type WorkflowImport } from './types';
import NodeDetails from './NodeDetails';
import OpenArticles from './OpenArticles';
import DirectoryField from './DirectoryField';
import DocumentDiff from './DocumentDiff';
import './editor.css';

export type Suggestion = { id: string; kind: 'expression' | 'structure' | 'consistency' | 'redaction'; title: string; reason: string; original: string; replacement: string; start: number | null; end: number | null; status: string; version: string; risk?: string; run_id?: string };
type Document = { workspace: string; path: string; revision: string; text: string; disk_text: string; disk_hash: string; updated_at: string; suggestions: Suggestion[]; check: { status: string } | null; workflow_import?: WorkflowImport };
type Version = { id: string; created_at: string; operation: string; text: string };
const kinds = { expression: '表达优化', structure: '结构建议', consistency: '一致性', redaction: '脱敏风险' };
const operations: Record<string, string> = { open: '打开', edit: '编辑快照', save: '保存文件', accept: '采用建议', ignore: '忽略建议', reject: '拒绝建议', restore: '恢复版本', external_reload: '读取外部修改', reload: '读取磁盘版本', before_reload: '读取磁盘前的草稿', before_workflow_apply: '载入工作流前的草稿', workflow_apply: '载入工作流稿件', check_started: '开始检查', check_completed: '完成检查', check_failed: '检查失败' };
const statuses: Record<string, string> = { pending: '待处理', stale: '待复查', accepted: '已采用', rejected: '已拒绝', ignored: '已忽略' };
const fileName = (path: string) => path.split(/[\\/]/).filter(Boolean).at(-1) || path;

export default function EditorWorkbench() {
  const [auxiliary, setAuxiliary] = useState<AuxiliaryView>('suggestions');
  const [auxiliaryOpen, setAuxiliaryOpen] = useState(true), [filesOpen, setFilesOpen] = useState(true);
  const [paneWidth, setPaneWidth] = useState(380), [paneHeight, setPaneHeight] = useState(340);
  const [newRun, setNewRun] = useState(false);
  const columns = useRef<HTMLDivElement>(null);
  const stacked = useMediaQuery('(max-width: 900px)');
  const [files, setFiles] = useState<string[]>([]), [root, setRoot] = useState(''), [query, setQuery] = useState('');
  const [doc, setDoc] = useState<Document | null>(null), [text, setText] = useState(''), [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selected, setSelected] = useState(''), [preview, setPreview] = useState(false), [busy, setBusy] = useState(false), [checking, setChecking] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [navigation, setNavigation] = useState(false), [settings, setSettings] = useState(false);
  const [versions, setVersions] = useState<Version[] | null>(null), [version, setVersion] = useState<Version | null>(null), [feedback, setFeedback] = useState(''), [rejecting, setRejecting] = useState<Suggestion | null>(null);
  const [conflict, setConflict] = useState<{ runId: string; path: string; text: string; candidate: string } | null>(null);
  const [processed, setProcessed] = useState(false);
  const editor = useRef<EditorHandle | null>(null), epoch = useRef(0), textRef = useRef(text), docRef = useRef(doc), inFlight = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const mobileFiles = useMediaQuery('(max-width: 900px)');
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const workflow = useWorkflow(doc?.path ?? null, root);
  const sessions = useRef(new Map<string, { doc: Document; text: string; suggestions: Suggestion[]; selected: string; preview: boolean; processed: boolean; auxiliary: AuxiliaryView; scroll: number }>());
  const [opened, setOpened] = useState<string[]>([]), [opening, setOpening] = useState(false);
  const [browsing, setBrowsing] = useState(false), [pickerError, setPickerError] = useState('');
  const [workspaceOpen, setWorkspaceOpen] = useState(false), [workspacePath, setWorkspacePath] = useState('');
  const [workspaceError, setWorkspaceError] = useState(''), [workspaceBusy, setWorkspaceBusy] = useState(false);
  const restored = useRef(false);
  const startAttempt = useRef<{ body: string; id: string } | null>(null);
  const editableRun = !!workflow.run && !terminal(workflow.run.status) && workflow.run.editor_source?.workspace === root && workflow.run.editor_source?.path === doc?.path;
  textRef.current = text; docRef.current = doc;
  const dirty = !!doc && text !== doc.disk_text;

  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (docRef.current && textRef.current !== docRef.current.text) { e.preventDefault(); e.returnValue = ''; }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, []);
  useEffect(() => {
    const search = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && !settings && versions === null && !rejecting) {
        e.preventDefault();
        if (mobileFiles) setNavigation(true);
        else { setFilesOpen(true); requestAnimationFrame(() => searchInput.current?.focus()); }
      }
    };
    window.addEventListener('keydown', search);
    return () => window.removeEventListener('keydown', search);
  }, [mobileFiles, settings, versions, rejecting]);

  async function loadFiles() {
    setBusy(true); setError('');
    try { const value = await api<{ root: string; files: string[] }>('/editor/files'); setWorkspace(value.root); setFiles(value.files); setRoot(value.root);
      if (!restored.current) {
        restored.current = true;
        try {
          const saved = JSON.parse(localStorage.getItem('inkflow-open:' + value.root) || '{}');
          const paths = Array.isArray(saved.paths) ? saved.paths.filter((p: string) => value.files.includes(p)) : [];
          setOpened(paths);
          const active = paths.includes(saved.current) ? saved.current : paths[0];
          if (active) { const document = await api<Document>(`/editor/document?path=${encodeURIComponent(active)}`); install(document); }
        } catch { /* an empty session remains usable */ }
      } }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { void loadFiles(); }, []);
  function install(value: Document) {
    setDoc(value); docRef.current = value; setText(value.text); textRef.current = value.text;
  }
  useEffect(() => {
    if (root && restored.current) localStorage.setItem('inkflow-open:' + root, JSON.stringify({ paths: opened, current: doc?.path }));
  }, [root, opened, doc?.path]);
  function chooseWorkspace() { setWorkspacePath(root); setWorkspaceError(''); setWorkspaceOpen(true); }
  async function switchWorkspace() {
    if (busy || browsing || workspaceBusy || inFlight.current) return;
    setWorkspaceError('');
    if (docRef.current && textRef.current !== docRef.current.text && !await operate('edit')) {
      setWorkspaceError('当前草稿尚未保存，请处理保存错误后重试。'); return;
    }
    setWorkspaceBusy(true);
    try {
      await api('/editor/workspace', { method: 'POST', body: JSON.stringify({ path: workspacePath }) });
      window.location.reload();
    } catch (error) { setWorkspaceError((error as Error).message); setWorkspaceBusy(false); }
  }
  async function browseArticle() {
    if (busy || browsing || inFlight.current) return;
    editor.current?.capture();
    setBrowsing(true); setPickerError('');
    try {
      const path = await chooseLocalPath('article', docRef.current?.path ?? '');
      if (path !== null) {
        setFiles(old => old.includes(path) ? old : [...old, path].sort());
        await open(path);
      }
    } catch (error) { setPickerError((error as Error).message); }
    finally { setBrowsing(false); }
  }
  useEffect(() => {
    if (!doc || text === doc.text || busy) return;
    const timer = setTimeout(() => void operate('edit'), 650);
    return () => clearTimeout(timer);
  }, [text, doc?.revision, busy]);
  useEffect(() => {
    const run = workflow.run;
    if (!doc || !run || run.editor_source?.path !== doc.path || run.editor_source.workspace !== root) { setSuggestions([]); return; }
    const items = run.annotations.map(s => ({ ...s }));
    if (textRef.current !== doc.text && !terminal(run.status)) for (const s of items) if (s.status === 'pending') s.status = 'stale';
    setSuggestions(items);
  }, [workflow.run?.id, workflow.run?.event_cursor, doc?.revision, root]);
  useEffect(() => {
    if (workflow.run?.pending?.kind === 'diff_review') { setAuxiliary('details'); setAuxiliaryOpen(true); }
  }, [workflow.run?.pending?.id]);
  async function open(path: string) {
    if (path === docRef.current?.path) { setNavigation(false); setOpening(false); return; }
    if (inFlight.current || busy) return;
    // 在保存草稿和临时切换编辑权限前固定滚动快照，避免失焦或首帧测量重置滚动。
    editor.current?.capture();
    const previous = docRef.current;
    if (previous) {
      const saved = textRef.current !== previous.text ? await operate('edit') : previous;
      if (!saved) return;
      sessions.current.set(previous.path, { doc: saved, text: textRef.current, suggestions, selected, preview, processed, auxiliary, scroll: document.querySelector('.document-preview')?.scrollTop ?? 0 });
    }
    const token = ++epoch.current; setBusy(true); setError(''); setNotice('');
    try {
      const value = await api<Document>(`/editor/document?path=${encodeURIComponent(path)}`);
      if (token !== epoch.current) return;
      const cached = sessions.current.get(path);
      install(value); setOpened(paths => paths.includes(path) ? paths : [...paths, path]);
      setSelected(cached?.selected ?? ''); setPreview(cached?.preview ?? false); setProcessed(cached?.processed ?? false); setAuxiliary(cached?.auxiliary ?? 'suggestions');
      setNavigation(false); setOpening(false);
      requestAnimationFrame(() => { const pane = document.querySelector('.document-preview'); if (pane && cached) pane.scrollTop = cached.scroll; });
    } catch (e) { if (epoch.current === token) { setError((e as Error).message); setPickerError((e as Error).message); } } finally { setBusy(false); }
  }
  async function closeArticle(path: string) {
    if (path === doc?.path) {
      editor.current?.capture();
      if (textRef.current !== doc.text && !await operate('edit')) return;
      const next = opened.find(p => p !== path);
      if (next) await open(next); else { ++epoch.current; setDoc(null); docRef.current = null; setText(''); setSuggestions([]); }
    }
    setOpened(paths => paths.filter(p => p !== path));
  }
  function changed(value: string, edits: { from: number; to: number; inserted: string }[]) {
    setText(value); textRef.current = value;
    setSuggestions(current => current.map(s => {
      if (s.status !== 'pending') return s;
      if (s.start === null || s.end === null) return { ...s, status: 'stale' };
      const a = s.start, b = s.end;
      const touched = edits.some(e => (e.from < b && e.to > a) || (e.from === e.to && e.from > a && e.from < b));
      const shift = edits.filter(e => e.to <= a).reduce((n, e) => n + e.inserted.length - (e.to - e.from), 0);
      const start = a + shift, end = b + shift;
      return { ...s, start, end, status: touched || value.slice(start, end) !== s.original ? 'stale' : 'pending' };
    }));
  }
  async function operate(action: string, extra: Record<string, unknown> = {}) {
    const current = docRef.current;
    if (!current || inFlight.current) return null;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    const original = textRef.current, token = epoch.current;
    try {
      if (['accept', 'ignore', 'reject'].includes(action)) {
        await workflow.act(action, { ...extra, editor: { path: current.path, revision: current.revision, text: original, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id) } });
        const value = await api<Document>(`/editor/document?path=${encodeURIComponent(current.path)}`);
        if (epoch.current === token) { if (textRef.current === original) install(value); else { setDoc(value); docRef.current = value; } }
        return value;
      }
      const value = await api<Document>('/editor/operation', { method: 'POST', body: JSON.stringify({ path: current.path, revision: current.revision, text: original, action, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id), ...extra }) });
      if (epoch.current === token) {
        // 模型等待期间允许打字，但绝不能让旧响应覆盖新编辑。
        if (textRef.current === original) install(value);
        else {
          setDoc(value); docRef.current = value;
          if (action === 'check') { setSuggestions(value.suggestions.map(s => ({ ...s, status: 'stale' }))); setNotice('检查期间正文已修改，结果需要重新检查。'); }
        }
      }
      return value;
    } catch (e) {
      setError((e as Error).message);
      // 检查失败仍可能已保存快照，更新 revision 并保留本地输入供重试。
      if (action === 'check') try {
        const value = await api<Document>(`/editor/document?path=${encodeURIComponent(current.path)}`);
        if (epoch.current === token) { setDoc(value); docRef.current = value; }
      } catch { /* 保留原始错误 */ }
      return null;
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function check(feedbackText = '') {
    if (!docRef.current || checking || inFlight.current) return;
    setChecking(true); setProcessed(false); setError('');
    const path = docRef.current.path, token = epoch.current;
    try {
      const old = workflow.active;
      if (old) {
        if (old.workflow_id !== 'full-check' || workflow.run?.id !== old.id || workflow.run.status !== 'waiting') throw new Error('请先完成或停止当前运行，再启动全文检查。');
        await workflow.act('finish_check');
        for (let i = 0; i < 100; i++) {
          const status = await api<Run>(`/runs/${old.id}`);
          if (terminal(status.status)) break;
          if (i === 99) throw new Error('上一轮尚未结束，请稍后重试。');
          await new Promise(resolve => setTimeout(resolve, 80));
        }
      }
      const saved = await operate('edit');
      if (!saved || epoch.current !== token || textRef.current !== saved.text) return;
      const data = { workflow_id: 'full-check', name: fileName(path), raw_text: saved.text, source_name: path, no_publish: true,
        editor_source: { path, revision: saved.revision }, rerun_of: (old ?? (workflow.articleRuns[path]?.workflow_id === 'full-check' ? workflow.articleRuns[path] : null))?.id, feedback: feedbackText };
      const body = JSON.stringify(data);
      if (startAttempt.current?.body !== body) startAttempt.current = { body, id: requestId() };
      const run = await api<Run>('/runs', { method: 'POST', body: JSON.stringify({ ...data, request_id: startAttempt.current.id }) });
      if (epoch.current === token) { workflow.created(run); showAuxiliary('suggestions'); }
    } catch (e) { if (epoch.current === token) setError((e as Error).message); }
    finally { setChecking(false); }
  }
  async function startWorkflow() {
    if (workflow.workflowId === 'full-check') { await check(); return; }
    const saved = await operate('edit'); if (saved && textRef.current === saved.text) setNewRun(true);
  }
  function scrollSuggestion(id: string) {
    requestAnimationFrame(() => document.getElementById(`suggestion-${id}`)?.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' }));
  }
  function locate(s: Suggestion) {
    setSelected(s.id); setPreview(false);
    if (s.status === 'pending' && editableRun) requestAnimationFrame(() => editor.current?.focus(s));
    scrollSuggestion(s.id);
  }
  async function history() {
    const value = await operate('edit'); if (!value) return;
    try { setVersions(await api<Version[]>(`/editor/versions?path=${encodeURIComponent(value.path)}`)); }
    catch (e) { setError((e as Error).message); }
  }
  async function applyWorkflowResult(snapshot: WorkflowApplyRequest) {
    const current = docRef.current;
    if (!current || inFlight.current || checking) throw new Error('文章正在处理中，请稍后重新对比。');
    if (current.path !== snapshot.path || current.revision !== snapshot.revision || textRef.current !== snapshot.text) throw new Error('编辑稿已变化，请关闭此对话框并重新对比。');
    inFlight.current = true; setBusy(true);
    try {
      const { runId, ...body } = snapshot;
      const value = await api<Document>(`/runs/${runId}/document-apply`, { method: 'POST', body: JSON.stringify({ ...body, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id) }) });
      if (docRef.current?.path === snapshot.path) {
        if (textRef.current === snapshot.text) { install(value); setPreview(false); setProcessed(false); }
        else { setDoc(value); docRef.current = value; setSuggestions(items => items.map(s => s.status === 'pending' ? { ...s, status: 'stale' } : s)); }
        setNotice('工作流稿件已载入，替换前草稿保留在历史中。可继续编辑，再保存或提交到工作流。');
      }
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function workflowAction(action: string, payload: Record<string, unknown> = {}) {
    const current = docRef.current;
    if (['finish', 'publish'].includes(action) && current?.workflow_import?.run_id === workflow.run?.id && current?.workflow_import?.kind === 'article') {
      return workflow.act(action, { ...payload, document: textRef.current });
    }
    if (action === 'accept' && workflow.run?.pending?.kind === 'diff_review' && current) {
      const saved = await operate('edit'); if (!saved || textRef.current !== saved.text) throw new Error('正文正在变化，请重新对比。');
      const snapshot = saved.text;
      try {
        await workflow.act(action, { ...payload, editor: { path: saved.path, revision: saved.revision, text: snapshot } });
      } catch (e) {
        if ((e as Error).message.includes('对比')) setConflict({ runId: workflow.run.id, path: saved.path, text: snapshot, candidate: workflow.run.pending.candidate_text ?? '' });
        throw e;
      }
      const value = await api<Document>(`/editor/document?path=${encodeURIComponent(saved.path)}`);
      if (docRef.current?.path === saved.path) {
        if (textRef.current === snapshot) { install(value); setPreview(false); }
        else { setDoc(value); docRef.current = value; setNotice('已确认稿已保留为版本；确认期间的新编辑仍在当前编辑区。'); }
      }
      return;
    }
    return workflow.act(action, payload);
  }
  async function submitWorkflowDocument() {
    const run = workflow.run, current = docRef.current, draft = textRef.current;
    if (!run?.pending || run.pending.kind !== 'final_review' || current?.workflow_import?.run_id !== run.id || current.workflow_import.kind !== 'article') throw new Error('请先载入本次工作流的文章审阅稿。');
    if (!await operate('edit')) throw new Error('未能保存编辑快照，请先处理文章错误。');
    if (textRef.current !== draft) throw new Error('提交期间正文已改变，请重新提交。');
    await workflow.act('save', { revision: run.pending.revision, document: draft });
    setNotice('编辑稿已提交到工作流，正在保存新的审阅版本。可在节点详情继续确认输出。');
  }
  const pending = suggestions.filter(s => ['pending', 'stale'].includes(s.status));
  const done = suggestions.filter(s => ['accepted', 'rejected', 'ignored', 'reviewed'].includes(s.status));
  const visible = processed ? done : pending;
  const matchingFiles = files.filter(p => p.toLowerCase().includes(query.toLowerCase()));
  function showAuxiliary(view: AuxiliaryView) { setAuxiliary(view); setAuxiliaryOpen(true); }
  function resizePane(event: PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const target = event.currentTarget;
    target.setPointerCapture(event.pointerId);
  }
  function movePane(event: PointerEvent<HTMLDivElement>) {
    if (!event.currentTarget.hasPointerCapture(event.pointerId) || !columns.current) return;
    const bounds = columns.current.getBoundingClientRect();
    if (stacked) setPaneHeight(Math.max(100, Math.min(bounds.height - 150, bounds.bottom - event.clientY)));
    else setPaneWidth(Math.max(288, Math.min(720, bounds.width - 360, bounds.right - event.clientX)));
  }
  const navigationPanel = <aside className="article-files" aria-label="文章文件">
    <div className="editor-brand"><span>InkFlow</span><IconButton aria-label="收起文件导航" onClick={() => mobileFiles ? setNavigation(false) : setFilesOpen(false)}><Close /></IconButton></div>
    <div className="workspace-path" title={root}><FolderOutlined fontSize="small" /><span>{fileName(root) || '本地工作区'}</span><Tooltip title="切换工作区"><span><IconButton size="small" aria-label="切换工作区" disabled={busy || browsing} onClick={chooseWorkspace}><FolderOpenOutlined fontSize="small" /></IconButton></span></Tooltip></div>
    <label className="file-search"><Search fontSize="small" /><input ref={searchInput} type="search" placeholder="搜索文章" aria-label="搜索文章" value={query} onChange={e => setQuery(e.target.value)} /><kbd>Ctrl K</kbd></label>
    <div className="file-list-heading"><span>已打开文章</span><span>{opened.length}</span><IconButton size="small" aria-label="刷新文件" disabled={busy || browsing} onClick={() => void loadFiles()}><Refresh fontSize="small" /></IconButton></div>
    <Button startIcon={<FolderOutlined />} disabled={busy || browsing} onClick={() => { setPickerError(''); setOpening(true); }}>打开文章</Button>
    <OpenArticles query={query} paths={opened.filter(p => p.toLowerCase().includes(query.toLowerCase()))} current={doc?.path} runs={workflow.articleRuns} busy={busy || browsing} onOpen={p => void open(p)} onClose={p => void closeArticle(p)} />
    <div className="file-footer">
      <Button startIcon={<HubOutlined />} onClick={() => { showAuxiliary('graph'); setNavigation(false); }}>工作流</Button>
      <div className="workspace-profile"><span className="workspace-avatar">I</span><span>本地工作区</span><Tooltip title="设置"><IconButton aria-label="设置" onClick={() => { setNavigation(false); setSettings(true); }}><SettingsOutlined fontSize="small" /></IconButton></Tooltip></div>
    </div>
  </aside>;

  return <div className={`editor-workbench ${!filesOpen ? 'files-collapsed' : ''}`}>
    <a href="#article-content" className="skip-link">跳到文章内容</a>
    {!mobileFiles && filesOpen && navigationPanel}
    <Drawer open={mobileFiles && navigation} onClose={() => setNavigation(false)} slotProps={{ paper: { className: 'files-drawer' }, transition: { onEntered: () => { if (!opening && !workspaceOpen && document.activeElement?.classList.contains('files-drawer')) searchInput.current?.focus(); } } }}>{navigationPanel}</Drawer>
    <div className="article-main">
      <header className="article-header">
        <IconButton aria-label={mobileFiles || !filesOpen ? '打开文件导航' : '收起文件导航'} aria-expanded={mobileFiles ? navigation : filesOpen} onClick={() => mobileFiles ? setNavigation(true) : setFilesOpen(value => !value)}><Menu fontSize="small" /></IconButton>
        <div className="article-breadcrumb"><ArticleOutlined fontSize="small" /><span>文章</span><ChevronRight fontSize="small" /><strong title={doc?.path}>{doc?.path || '选择一篇文章'}</strong></div>
        <div className="article-header-actions">
          <Button className="auxiliary-toggle" aria-expanded={auxiliaryOpen} aria-controls="workbench-auxiliary" onClick={() => setAuxiliaryOpen(value => !value)}>{auxiliaryOpen ? '收起辅助窗格' : '展开辅助窗格'}</Button>
          <IconButton aria-label="工作区设置" onClick={() => setSettings(true)}><SettingsOutlined fontSize="small" /></IconButton>
          <span role="status" className={`save-state ${dirty ? 'is-dirty' : ''}`}>{checking ? '全文检查中' : busy ? '处理中…' : !doc ? '' : dirty ? '未保存' : <><Check />已保存</>}</span>
          <Tooltip title="文章历史"><span><Button className="history-button" startIcon={<HistoryOutlined />} disabled={!doc || busy} onClick={() => void history()}>历史</Button></span></Tooltip>
          <Button variant="contained" disabled={!doc || busy || browsing} onClick={() => void operate('save').then(v => { if (v) setNotice('文件已保存。'); })}>保存</Button>
        </div>
        {busy && <LinearProgress className="editor-progress" aria-label="操作进行中" />}
      </header>
      <WorkflowControls controller={workflow} onStart={() => void startWorkflow()} onPending={() => { workflow.setSelected(workflow.run?.pending?.node ?? null); showAuxiliary(workflow.run?.pending?.kind === 'suggestion_review' ? 'suggestions' : 'details'); }} />
      <WorkflowDocumentBridge run={workflow.run && !terminal(workflow.run.status) && workflow.run.document_result?.kind !== 'redaction' ? workflow.run : null} target={doc} text={text} busy={busy || checking || workflow.busy} onOpen={path => void open(path)} onChoose={() => mobileFiles ? setNavigation(true) : setFilesOpen(true)} onApply={applyWorkflowResult} onSubmit={submitWorkflowDocument} />
      {workflow.error && <Alert severity="error" className="editor-alert" action={<Button onClick={workflow.retry}>重试</Button>} onClose={() => workflow.setError('')}>{workflow.error}</Alert>}
      {error && <Alert severity="error" className="editor-alert" action={<Button disabled={busy} onClick={() => {
        if (!doc) void loadFiles();
        else if (window.confirm('读取磁盘文件替换编辑区？当前草稿将保留在历史中。')) void operate('reload');
      }}>{doc ? '读取磁盘版本' : '重试'}</Button>} onClose={() => setError('')}>{error}</Alert>}
      {notice && <Alert severity="info" className="editor-alert" onClose={() => setNotice('')}>{notice}</Alert>}
      <div ref={columns} className={`article-columns ${!auxiliaryOpen ? 'auxiliary-collapsed' : ''}`} style={{ '--auxiliary-width': `${paneWidth}px`, '--auxiliary-height': `${paneHeight}px` } as CSSProperties}>
        <main id="article-content" tabIndex={-1} className="article-body" aria-label="文章编辑区">
          <div className="article-toolbar">
            <div className="editor-segmented" role="group" aria-label="文章视图"><button disabled={!doc} aria-pressed={!preview} onClick={() => setPreview(false)}>编辑</button><button disabled={!doc} aria-pressed={preview} onClick={() => setPreview(true)}>预览</button></div>
            <span className="toolbar-spacer" />
            <Tooltip title="撤销"><span><IconButton aria-label="撤销" disabled={!doc || preview || busy} onClick={() => editor.current?.undo()}><Undo fontSize="small" /></IconButton></span></Tooltip>
            <Tooltip title="重做"><span><IconButton aria-label="重做" disabled={!doc || preview || busy} onClick={() => editor.current?.redo()}><Redo fontSize="small" /></IconButton></span></Tooltip>
          </div>
          {!doc ? <div className="article-paper editor-empty large"><ArticleOutlined /><h1>{busy ? '正在读取文章' : '从一篇文章开始'}</h1><p>打开本地 Markdown 或 MDX，<br />专注写作，逐条打磨。</p><Button variant="outlined" disabled={busy || browsing} startIcon={<FolderOutlined />} onClick={() => { setPickerError(''); setOpening(true); }}>选择文章</Button><Button disabled={busy || browsing} startIcon={<FolderOpenOutlined />} onClick={chooseWorkspace}>选择工作区文件夹</Button></div> : <>
            <div className="article-paper" hidden={preview}><ArticleEditor key={root + doc.path} documentKey={root + "|" + doc.path} text={text} suggestions={editableRun ? suggestions : []} selected={selected} readOnly={browsing || busy && !checking} onChange={changed} onSelect={id => { setSelected(id); setProcessed(false); showAuxiliary('suggestions'); scrollSuggestion(id); }} handle={editor} /></div>
            {preview && <article className="article-paper document-preview"><MarkdownPreview text={text.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n---\r?\n/, '')} /></article>}
            {preview && /\.mdx$/i.test(doc.path) && <small className="preview-limit">MDX 组件仅保留原文，不执行 JavaScript。</small>}
          </>}
          <footer className="article-status"><span>{doc ? `${doc.path.split('.').at(-1)?.toUpperCase()} · ${Array.from(text).length.toLocaleString()} 字符` : 'Markdown / MDX'}</span><span>{doc ? `版本 ${doc.revision.slice(0, 8)}` : '修改由你确认'}</span></footer>
        </main>
        <div className="pane-resizer" hidden={!auxiliaryOpen} role="separator" tabIndex={0} aria-label="调整辅助窗格大小" aria-orientation={stacked ? 'horizontal' : 'vertical'} aria-valuemin={stacked ? 100 : 288} aria-valuemax={stacked ? 600 : 720} aria-valuenow={stacked ? paneHeight : paneWidth}
          onPointerDown={resizePane} onPointerMove={movePane} onPointerUp={e => e.currentTarget.releasePointerCapture(e.pointerId)}
          onKeyDown={e => {
            if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return;
            e.preventDefault();
            const extent = columns.current?.getBoundingClientRect();
            const max = stacked ? Math.max(100, (extent?.height ?? 750) - 150) : Math.max(288, Math.min(720, (extent?.width ?? 1080) - 360));
            const update = (value: number) => e.key === 'Home' ? (stacked ? 100 : 288) : e.key === 'End' ? max : Math.max(stacked ? 100 : 288, Math.min(max, value + (['ArrowLeft', 'ArrowUp'].includes(e.key) ? 32 : -32)));
            if (stacked) setPaneHeight(update); else setPaneWidth(update);
          }} />
        <aside id="workbench-auxiliary" className="workbench-auxiliary" hidden={!auxiliaryOpen} aria-label="辅助窗格">
          <div className="auxiliary-tabs" role="tablist" aria-label="辅助内容">{([['suggestions', '检查建议'], ['graph', '流程图'], ['details', '节点详情'], ['runs', '运行记录']] as const).map(([id, label]) => <button id={`auxiliary-tab-${id}`} key={id} role="tab" aria-selected={auxiliary === id} aria-controls={`auxiliary-panel-${id}`} tabIndex={auxiliary === id ? 0 : -1} onClick={() => setAuxiliary(id)} onKeyDown={e => {
            const ids: AuxiliaryView[] = ['suggestions', 'graph', 'details', 'runs'];
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
            e.preventDefault(); const next = e.key === 'Home' ? ids[0] : e.key === 'End' ? ids[3] : ids[(ids.indexOf(id) + (e.key === 'ArrowRight' ? 1 : 3)) % 4];
            setAuxiliary(next); document.getElementById(`auxiliary-tab-${next}`)?.focus();
          }}>{label}{id === 'suggestions' && pending.length > 0 ? ` ${pending.length}` : ''}{id === 'details' && workflow.run?.pending ? ' · 待办' : ''}</button>)}</div>
        <section id="auxiliary-panel-suggestions" role="tabpanel" aria-labelledby="auxiliary-tab-suggestions" className="article-suggestions" hidden={auxiliary !== 'suggestions'}>
          <div className="suggestions-heading"><h2>{workflow.run?.workflow_id === "reviewed-publishing" ? "脱敏审核" : "全文检查"} <span>{pending.length}</span></h2><Button disabled={!doc || busy || checking || !!workflow.active && (workflow.active.workflow_id !== "full-check" || workflow.run?.id !== workflow.active.id || workflow.run?.status !== "waiting")} onClick={() => void check()}>{checking ? '检查中…' : '重新检查'}</Button></div>
          {workflow.run && <Button size="small" onClick={() => { setVersions([{ id: workflow.run!.id, created_at: workflow.run!.created_at, operation: '检查输入快照', text: workflow.run!.annotation_snapshot }]); setVersion(null); }}>查看检查快照</Button>}
          <div className="suggestion-tabs" role="group" aria-label="建议状态"><button aria-pressed={!processed} onClick={() => setProcessed(false)}>待处理 <span>{pending.length}</span></button><button aria-pressed={processed} onClick={() => setProcessed(true)}>已处理 <span>{done.length}</span></button></div>
          <div className="suggestion-list" aria-busy={checking || workflow.run?.workflow_id === 'full-check' && workflow.run.status === 'running'}>
            {workflow.run?.state.failure && <Alert severity="error">{workflow.run.state.failure}</Alert>}
            {workflow.run?.status === 'interrupted' && <Alert severity="warning">服务重启已中断本轮，请基于当前工作副本重新检查。</Alert>}
            {workflow.run?.status === 'stopped' && <Alert severity="info">本轮已停止，可以重新检查。</Alert>}
            {!visible.length && <div className="suggestions-empty" role="status"><Check /><strong>{checking ? '正在通读你的文章' : processed ? '暂无已处理建议' : pending.length === 0 && done.length ? '建议已全部处理' : workflow.run?.pending?.kind === 'suggestion_review' ? '本轮未发现建议，可结束本轮' : workflow.run?.status === 'running' ? '正在检查固定快照' : '让文章更进一步'}</strong><p>{checking ? '检查完整正文后，建议会出现在这里。' : processed ? '采用、忽略或反馈后，可在这里回看。' : doc ? '修改后可以重新检查，继续打磨表达与结构。' : '选择文章，再开始全文检查。'}</p></div>}
            {visible.map(s => {
              const expanded = selected === s.id;
              return <section id={`suggestion-${s.id}`} key={s.id} className={`suggestion-card ${s.kind} ${expanded ? 'expanded' : ''}`}>
                <button className="suggestion-title" aria-expanded={expanded} aria-controls={`suggestion-content-${s.id}`} onClick={() => expanded ? setSelected('') : locate(s)}>
                  <span className="suggestion-number">{String(suggestions.indexOf(s) + 1).padStart(2, '0')}</span><span><strong>{s.title}</strong><small>{kinds[s.kind]}{s.start === null ? ' · 全文' : ''}</small></span><ChevronRight className="suggestion-chevron" fontSize="small" />
                </button>
                <div id={`suggestion-content-${s.id}`} className="suggestion-content" hidden={!expanded}>
                  <div className="suggestion-meta"><span>{kinds[s.kind]}{s.start === null ? ' · 全文' : ''}</span><Chip size="small" className="suggestion-state" label={statuses[s.status] || s.status} /></div>
                  {workflow.run?.feedback_history?.filter(f => f.suggestion_id === s.id).map((f, i) => <p className="suggestion-reason" key={i}>反馈：{f.feedback}</p>)}
                  <p className="suggestion-reason">{s.risk && `风险：${({ high: "高", medium: "中", low: "低" } as Record<string, string>)[s.risk]} · `}{s.reason}</p>
                  {s.original && <><span className="suggestion-field">原文</span><blockquote>{s.original}</blockquote></>}
                  <div className="suggestion-replacement"><span className="suggestion-field">{s.start === null ? '建议' : '替换为'}</span><p>{s.replacement || '（删除该范围）'}</p></div>
                  {editableRun && s.kind !== 'redaction' && ['pending', 'stale'].includes(s.status) && <>
                    <Button fullWidth variant="contained" disabled={busy || s.status !== 'pending' || s.start === null} onClick={() => void operate('accept', { suggestion_id: s.id })}>采用修改</Button>
                    {s.start === null && <p className="suggestion-help">全文结构建议请手动调整正文后重新检查。</p>}
                    <div className="suggestion-actions"><Button disabled={busy} onClick={() => { setRejecting(s); setFeedback(''); }}>拒绝并反馈</Button><Button disabled={busy} onClick={() => void operate('ignore', { suggestion_id: s.id })}>忽略</Button></div>
                    {s.status === 'stale' && <div className="suggestion-stale"><span>原文已修改，请复查后再采用。</span><Button disabled={busy} onClick={() => void check()}>重新检查全文</Button></div>}
                  </>}
                </div>
                {!expanded && s.status !== 'pending' && <span className="collapsed-state">{statuses[s.status] || s.status}</span>}
              </section>;
            })}
          </div>
          {workflow.run?.workflow_id === 'reviewed-publishing' && auxiliary === 'suggestions' && <NodeDetails workflow={workflow.run.workflow} run={workflow.run} selected={workflow.run.pending?.node ?? workflow.run.current_node} busy={workflow.busy || busy} onAction={workflowAction} onSelect={workflow.setSelected} />}
          <footer className="review-footer" role="status">{workflow.run ? `${workflow.run.workflow.name} · ${workflow.run.status === 'waiting' ? '等待人工处理' : ({ running: '运行中', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断', stopping: '停止中' } as Record<string, string>)[workflow.run.status] ?? workflow.run.status}` : doc?.check ? ({ running: '检查中', failed: '检查失败 · 可重试', interrupted: '检查已中断 · 请重试', completed: '检查完成 · 修改由你确认' }[doc.check.status] || doc.check.status) : '建议供参考，保留你的表达。'}</footer>
        </section>
        <WorkflowPanel controller={{ ...workflow, act: workflowAction }} view={auxiliary} onView={setAuxiliary} />
        </aside>
      </div>
    </div>
    <Dialog open={!!conflict} onClose={() => setConflict(null)} fullWidth maxWidth="lg" aria-labelledby="redaction-conflict-title"><DialogTitle id="redaction-conflict-title">脱敏候选与当前稿件冲突</DialogTitle><DialogContent><p>工作副本已在检查后修改。核对两份正文，确认替换会保留当前稿件的历史版本。</p>{conflict && <DocumentDiff before={conflict.text} after={conflict.candidate} beforeLabel="当前工作副本" afterLabel="候选脱敏稿" initialLayout="split" expandable={false} />}{conflict?.text !== text && <Alert severity="warning">正文再次变化，请重新对比后确认。</Alert>}</DialogContent><DialogActions><Button onClick={() => setConflict(null)}>返回重新改写</Button><Button onClick={() => setConflict(old => old ? { ...old, text } : null)}>重新对比当前稿</Button><Button variant="contained" disabled={busy || workflow.busy || conflict?.text !== text || conflict?.path !== doc?.path || conflict?.runId !== workflow.run?.id} onClick={() => void workflowAction('accept', { confirm_conflict: true }).then(() => setConflict(null)).catch(e => setError(e.message))}>确认替换并保留历史</Button></DialogActions></Dialog>
    <Dialog open={opening} onClose={browsing ? undefined : () => setOpening(false)} fullWidth aria-labelledby="open-article-title"><DialogTitle id="open-article-title">打开文章</DialogTitle><DialogContent>
      <div className="article-picker-actions"><Button variant="outlined" startIcon={<FolderOpenOutlined />} disabled={busy || browsing} onClick={() => void browseArticle()}>{browsing ? '正在选择文件…' : '浏览文件…'}</Button><Button disabled={busy || browsing} onClick={() => { setOpening(false); chooseWorkspace(); }}>切换工作区</Button></div>
      {pickerError && <Alert severity="error" sx={{ mb: 2 }}>{pickerError}</Alert>}
      <TextField autoFocus fullWidth label="搜索工作区文章" value={query} disabled={browsing} onChange={e => setQuery(e.target.value)} /><div className="file-list workspace-picker">{matchingFiles.map(p => <button key={p} disabled={busy || browsing} onClick={() => void open(p)}>{p}{opened.includes(p) ? ' · 已打开' : ''}</button>)}{!matchingFiles.length && <p>{files.length ? '没有匹配文章，可浏览文件选择。' : '当前工作区没有文章，可切换工作区文件夹。'}</p>}</div></DialogContent><DialogActions><Button disabled={browsing} onClick={() => setOpening(false)}>关闭</Button></DialogActions></Dialog>
    <Dialog open={workspaceOpen} onClose={workspaceBusy ? undefined : () => setWorkspaceOpen(false)} fullWidth aria-labelledby="workspace-title"><DialogTitle id="workspace-title">切换工作区</DialogTitle><DialogContent><div className="form-stack">
      {workspaceError && <Alert severity="error">{workspaceError}</Alert>}
      <DirectoryField label="工作区目录" value={workspacePath} purpose="workspace" disabled={workspaceBusy} onPicking={setWorkspaceBusy} onChange={setWorkspacePath} onError={setWorkspaceError} />
      <p>切换前会保存当前草稿。请先结束所有运行中或等待处理的任务；工作区选择仅对本次启动有效。</p>
    </div></DialogContent><DialogActions><Button disabled={workspaceBusy} onClick={() => setWorkspaceOpen(false)}>取消</Button><Button variant="contained" disabled={busy || workspaceBusy || !workspacePath.trim() || workspacePath === root} onClick={() => void switchWorkspace()}>切换工作区</Button></DialogActions></Dialog>
    {newRun && workflow.workflows.find(w => w.id === workflow.workflowId) && <NewRunDialog open workflow={workflow.workflows.find(w => w.id === workflow.workflowId)!} currentDocument={doc ? { path: doc.path, revision: doc.revision, text } : undefined} onClose={() => setNewRun(false)} onCreated={value => { workflow.created(value); setNewRun(false); showAuxiliary('suggestions'); }} />}
    <Dialog open={!!rejecting} onClose={() => !busy && setRejecting(null)} fullWidth aria-labelledby="feedback-title"><DialogTitle id="feedback-title">拒绝并反馈</DialogTitle><DialogContent><div className="feedback-form"><p>说明你希望保留或调整的内容，重新检查时会参考这份反馈。</p><TextField autoFocus multiline minRows={4} label="重新生成的要求" value={feedback} onChange={e => setFeedback(e.target.value)} helperText={`${feedback.length} / 3000`} slotProps={{ htmlInput: { maxLength: 3000 } }} /></div></DialogContent><DialogActions><Button disabled={busy} onClick={() => setRejecting(null)}>取消</Button><Button variant="contained" disabled={busy || !feedback.trim()} onClick={() => {
      const s = rejecting!;
      void operate('reject', { suggestion_id: s.id, feedback }).then(async v => {
        if (v) { setRejecting(null); await check(`用户拒绝了建议「${s.title.slice(0, 200)}」。\n要求：${feedback}`); }
      });
    }}>拒绝并重新检查</Button></DialogActions></Dialog>
    <Dialog open={versions !== null} onClose={() => { setVersions(null); setVersion(null); }} fullWidth maxWidth="md" aria-labelledby="versions-title"><DialogTitle id="versions-title">文章历史</DialogTitle><DialogContent><p className="version-description">选择一个版本查看内容，恢复后可继续编辑，再手动保存到文件。</p><div className="version-layout"><div className="version-list">{versions?.map(v => <button key={v.id} aria-pressed={version?.id === v.id} onClick={() => setVersion(v)}><strong>{operations[v.operation] || v.operation}</strong><time>{formatTime(v.created_at)}</time><small>{v.id.slice(0, 8)}</small></button>)}</div><pre>{version ? version.text || '（空文档）' : '选择版本查看原文'}</pre></div></DialogContent><DialogActions><Button onClick={() => { setVersions(null); setVersion(null); }}>关闭</Button><Button variant="contained" disabled={!version || busy || version.operation === "检查输入快照"} onClick={() => {
      if (dirty && !window.confirm('恢复版本会替换当前未保存正文，当前内容已记录在历史中。继续？')) return;
      void operate('restore', { version: version!.id }).then(v => { if (v) { setVersions(null); setVersion(null); setNotice('已恢复到编辑区，可复查后保存文件。'); } });
    }}>恢复到编辑区</Button></DialogActions></Dialog>
    <Dialog open={settings} onClose={() => setSettings(false)} fullWidth maxWidth="md" aria-labelledby="editor-settings-title" className="editor-settings-dialog"><DialogTitle id="editor-settings-title">工作区设置</DialogTitle><DialogContent><SettingsPage /></DialogContent><DialogActions><Button onClick={() => setSettings(false)}>关闭</Button></DialogActions></Dialog>
  </div>;
}
