import { navigate, readRoute, useRoute, type Page } from './navigation';
import RunCenter from './RunCenter';
import AutomationCenter from './AutomationCenter';
import WorkflowStudio, { type StudioTarget } from './WorkflowStudio';
import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from 'react';
import { Alert, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Drawer, IconButton, LinearProgress, Tooltip, TextField, useMediaQuery } from '@mui/material';
import ArticleOutlined from '@mui/icons-material/ArticleOutlined';
import Check from '@mui/icons-material/Check';
import ChevronRight from '@mui/icons-material/ChevronRight';
import Close from '@mui/icons-material/Close';
import DeleteOutline from '@mui/icons-material/DeleteOutline';
import FolderOutlined from '@mui/icons-material/FolderOutlined';
import FolderOpenOutlined from '@mui/icons-material/FolderOpenOutlined';
import HistoryOutlined from '@mui/icons-material/HistoryOutlined';
import Menu from '@mui/icons-material/Menu';
import Redo from '@mui/icons-material/Redo';
import Refresh from '@mui/icons-material/Refresh';
import Search from '@mui/icons-material/Search';
import Undo from '@mui/icons-material/Undo';
import { api, formatTime, requestId } from './api';
import ArticleEditor, { forgetEditorSession, type EditorHandle } from './SourceEditor';
import MarkdownPreview from './MarkdownPreview';
import SettingsPage from './SettingsPage';
import WorkflowPanel, { WorkflowControls, type AuxiliaryView } from './App';
import NewRunDialog from './NewRunDialog';
import { useWorkflow } from './useWorkflow';
import WorkflowDocumentBridge from './WorkflowDocumentBridge';
import { terminal, type Run, type WorkflowApplyRequest, type WorkflowImport } from './types';
import NodeDetails from './NodeDetails';
import OpenArticles, { ArticleFileButton, articleName, type DocumentSummary } from './OpenArticles';
import DirectoryField from './DirectoryField';
import DocumentDiff from './DocumentDiff';
import './editor.css';

export type Suggestion = { id: string; kind: 'expression' | 'structure' | 'consistency' | 'redaction'; title: string; reason: string; original: string; replacement: string; start: number | null; end: number | null; status: string; version: string; risk?: string; run_id?: string };
type Document = { document_id: string; location: string | null; draft_pending?: boolean; file_error?: string | null; disk_conflict?: boolean; migration_conflicts?: { legacy_key: string; version: string }[]; workspace: string; path: string; revision: string; text: string; disk_text: string; disk_hash: string; updated_at: string; suggestions: Suggestion[]; check: { status: string } | null; workflow_import?: WorkflowImport };
type Version = { id: string; created_at: string; operation: string; text: string };
const kinds = { expression: '表达优化', structure: '结构建议', consistency: '一致性', redaction: '脱敏风险' };
const operations: Record<string, string> = { relocate: '重新定位文件', new: '新建草稿', first_save: '首次保存到文件', save_as: '另存为新文件', migration_snapshot: '迁移保留稿', migration_conflict: '迁移冲突草稿', open: '打开', edit: '编辑快照', save: '保存文件', accept: '采用建议', ignore: '忽略建议', reject: '拒绝建议', restore: '恢复版本', external_reload: '读取外部修改', reload: '读取磁盘版本', before_reload: '读取磁盘前的草稿', before_workflow_apply: '载入工作流前的草稿', workflow_apply: '载入工作流稿件', check_started: '开始检查', check_completed: '完成检查', check_failed: '检查失败' };
const statuses: Record<string, string> = { pending: '待处理', stale: '待复查', accepted: '已采用', rejected: '已拒绝', ignored: '已忽略' };
const fileName = (path: string) => path.split(/[\\/]/).filter(Boolean).at(-1) || path;
const parentDirectory = (path: string) => { const index = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')); return path.slice(0, index === 0 || index === 2 && path[1] === ':' ? index + 1 : index); };

export default function EditorWorkbench() {
  const [auxiliary, setAuxiliary] = useState<AuxiliaryView>('suggestions');
  const [auxiliaryOpen, setAuxiliaryOpen] = useState(true), [filesOpen, setFilesOpen] = useState(true);
  const [paneWidth, setPaneWidth] = useState(380), [paneHeight, setPaneHeight] = useState(340);
  const route = useRoute();
  const studioOpen = route.page === 'workflows';
  const runCenterOpen = route.page === 'tasks' && route.tab === 'runs';
  const automationOpen = route.page === 'tasks' && route.tab === 'watch';
  const articleVisible = route.page === 'articles';
  const reviewOrigin = useRef<string | null>(sessionStorage.getItem('inkflow-review-origin'));
  function go(page: Page) { editor.current?.capture(); setNavigation(false); navigate({ page }); }
  function reviewDocument(id: string, runId = '') {
    reviewOrigin.current = window.location.search;
    sessionStorage.setItem('inkflow-review-origin', window.location.search);
    if (runId) rememberRun(id, runId);
    navigate({ page: 'articles', document: id, articleRun: runId });
  }
  async function reviewRun(run: import('./types').RunSummary) { if (run.editor_source) reviewDocument(run.editor_source.document_id, run.id); }
  useEffect(() => {
    const frame = requestAnimationFrame(() => document.querySelector<HTMLElement>('[data-page]:not([hidden]) [data-page-heading]')?.focus());
    return () => cancelAnimationFrame(frame);
  }, [route.page, route.tab, route.ai]);
  const [studioTarget, setStudioTarget] = useState<StudioTarget | undefined>();
  useEffect(() => {
    const openStudio = (event: Event) => { const target = (event as CustomEvent<StudioTarget>).detail; setStudioTarget(target); navigate({ page: 'workflows', workflow: target.workflowId, stage: 'design', ai: '' }); };
    window.addEventListener('inkflow-open-studio', openStudio);
    return () => window.removeEventListener('inkflow-open-studio', openStudio);
  }, []);
  const [newRun, setNewRun] = useState(false);
  const columns = useRef<HTMLDivElement>(null);
  const stacked = useMediaQuery('(max-width: 900px)');
  const [defaultDirectory, setDefaultDirectory] = useState(''), [query, setQuery] = useState('');
  const [doc, setDoc] = useState<Document | null>(null), [text, setText] = useState(''), [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selected, setSelected] = useState(''), [preview, setPreview] = useState(false), [busy, setBusy] = useState(false), [checking, setChecking] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [navigation, setNavigation] = useState(false);
  const [versions, setVersions] = useState<Version[] | null>(null), [version, setVersion] = useState<Version | null>(null), [feedback, setFeedback] = useState(''), [rejecting, setRejecting] = useState<Suggestion | null>(null);
  const [conflict, setConflict] = useState<{ runId: string; path: string; text: string; candidate: string } | null>(null);
  const [processed, setProcessed] = useState(false);
  const editor = useRef<EditorHandle | null>(null), epoch = useRef(0), textRef = useRef(text), docRef = useRef(doc), inFlight = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const mobileFiles = useMediaQuery('(max-width: 900px)');
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const workflow = useWorkflow(doc?.document_id ?? null);
  const sessions = useRef(new Map<string, { doc: Document; text: string; suggestions: Suggestion[]; selected: string; preview: boolean; processed: boolean; auxiliary: AuxiliaryView; scroll: number }>());
  const [opened, setOpened] = useState<string[]>([]), [opening, setOpening] = useState(false);
  const [browsing, setBrowsing] = useState(false), [pickerError, setPickerError] = useState('');
  const [directoryOpen, setDirectoryOpen] = useState(false), [directoryPath, setDirectoryPath] = useState('');
  const [directoryError, setDirectoryError] = useState(''), [directoryBusy, setDirectoryBusy] = useState(false);
  const [relocating, setRelocating] = useState(false), [relocationPath, setRelocationPath] = useState(''), [relocationError, setRelocationError] = useState('');
  const restored = useRef(false);
  const restoredDocumentAttempt = useRef('');
  function rememberRun(documentId: string, runId: string) {
    try { const choices = JSON.parse(localStorage.getItem('inkflow-run-choices:documents') || '{}'); choices[documentId] = runId; localStorage.setItem('inkflow-run-choices:documents', JSON.stringify(choices)); } catch { /* in-memory selection still works */ }
  }
  const startAttempt = useRef<{ body: string; id: string } | null>(null);
  const editableRun = !!workflow.run && !terminal(workflow.run.status) && workflow.run.editor_source?.document_id === doc?.document_id;
  textRef.current = text; docRef.current = doc;
  const dirty = !!doc && (!!doc.draft_pending || !doc.location || text !== doc.disk_text);
  const [catalog, setCatalog] = useState<DocumentSummary[]>([]);
  const [clearedRecent, setClearedRecent] = useState<string[]>(() => {
    try { const value: unknown = JSON.parse(localStorage.getItem('inkflow-cleared-recent') || '[]'); return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []; } catch { return []; }
  });
  const [deletingDraft, setDeletingDraft] = useState<DocumentSummary | null>(null), [deleteError, setDeleteError] = useState('');
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [saveTarget, setSaveTarget] = useState<string | null>(null), [saveDirectory, setSaveDirectory] = useState('');
  const [saving, setSaving] = useState<'draft' | 'file' | null>(null), [saveError, setSaveError] = useState('');
  const [locationOpen, setLocationOpen] = useState(false), [filePath, setFilePath] = useState('');
  const [switchingDocument, setSwitchingDocument] = useState(false);
  const summaries = catalog.map(item => item.document_id === doc?.document_id ? { ...item, path: doc.path, location: doc.location, draft: dirty, file_error: doc.file_error } : item);
  const recentArticles = summaries.filter(d => !opened.includes(d.document_id) && (!d.location || !clearedRecent.includes(d.document_id)) && d.path.toLowerCase().includes(query.trim().toLowerCase())).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const saveLabel = saveError ? '保存失败／冲突' : saving === 'file' ? '保存文件中…' : saving === 'draft' || doc && text !== doc.text ? '草稿保存中…' : doc?.file_error ? '文件失联 · 草稿已保留' : doc?.disk_conflict ? '磁盘冲突 · 草稿已保留' : dirty ? '草稿已保留 · 未写回文件' : doc ? '已保存到文件' : '';

  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (docRef.current && textRef.current !== docRef.current.text) { e.preventDefault(); e.returnValue = ''; }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, []);
  useEffect(() => {
    const search = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && articleVisible && versions === null && !rejecting) {
        e.preventDefault();
        if (mobileFiles) setNavigation(true);
        else { setFilesOpen(true); requestAnimationFrame(() => searchInput.current?.focus()); }
      }
    };
    window.addEventListener('keydown', search);
    return () => window.removeEventListener('keydown', search);
  }, [mobileFiles, articleVisible, versions, rejecting]);

  async function refreshCatalog() { const rows = await api<DocumentSummary[]>('/documents'); setCatalog(rows); return rows; }
  async function loadFiles() {
    setBusy(true); setError('');
    try {
      const [spaces, documents] = await Promise.all([api<{ default_id: string; legacy_root: string; workspaces: { id: string; root: string }[] }>('/workspaces'), refreshCatalog()]);
      const legacySpace = spaces.workspaces.find(w => w.id === localStorage.getItem('inkflow-workspace'));
      const initialDirectory = localStorage.getItem('inkflow-default-directory') || legacySpace?.root || spaces.legacy_root;
      setDefaultDirectory(initialDirectory);
      localStorage.setItem('inkflow-default-directory', initialDirectory);
      if (restored.current && docRef.current) {
        const previous = docRef.current, original = textRef.current;
        const value = await api<Document>(`/documents/${previous.document_id}`);
        if (docRef.current?.document_id === previous.document_id) {
          if (textRef.current === original && original === previous.text) install(value);
          else { const retained = { ...previous, file_error: value.file_error, disk_conflict: value.disk_conflict }; setDoc(retained); docRef.current = retained; }
        }
      }
      if (!restored.current) {
        let saved: { ids?: string[]; current?: string } = {};
        try { saved = JSON.parse(localStorage.getItem('inkflow-open:documents') || '{}'); } catch { /* invalid browser cache */ }
        // On upgrade include all registered articles once; subsequent removals stay removed.
        const migrated = localStorage.getItem('inkflow-article-tree') === '1';
        const ids = [...new Set([...(saved.ids ?? []), ...(!migrated ? documents.map(d => d.document_id) : [])])].filter(id => documents.some(d => d.document_id === id));
        // 旧浏览器会话按授权工作区路径迁移，服务端负责合并身份。
        if (!saved.ids) {
          const space = legacySpace ?? spaces.workspaces.find(w => w.id === spaces.default_id)!;
          try {
            const legacy = JSON.parse(localStorage.getItem('inkflow-open:' + space.root) || localStorage.getItem('inkflow-open:' + spaces.legacy_root) || '{}');
            for (const path of legacy.paths ?? []) {
              const value = await api<Document>(`/workspaces/${space.id}/open`, { method: 'POST', body: JSON.stringify({ path }) });
              if (!ids.includes(value.document_id)) ids.push(value.document_id);
              if (path === legacy.current) saved.current = value.document_id;
            }
            await refreshCatalog();
          } catch { setNotice('部分旧会话文件无法打开，可从原位置重新选择；历史仍保留。'); }
        }
        if (!saved.ids && !ids.length) ids.push(...documents.filter(d => !d.location).map(d => d.document_id));
        setOpened(ids);
        const requested = readRoute().document;
        const active = documents.some(d => d.document_id === requested) ? requested : ids.includes(saved.current ?? '') ? saved.current : ids[0];
        if (active && !ids.includes(active)) setOpened(old => [...old, active]);
        if (active) { if (!readRoute().document && readRoute().page === 'articles') navigate({ document: active }, true); if (readRoute().articleRun) rememberRun(active, readRoute().articleRun); install(await api<Document>(`/documents/${active}`)); }
        restored.current = true;
        localStorage.setItem('inkflow-article-tree', '1');
      }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { void loadFiles(); }, []);
  function install(value: Document) {
    setDoc(value); docRef.current = value; setText(value.text); textRef.current = value.text;
    setCatalog(old => [...old.filter(d => d.document_id !== value.document_id), { document_id: value.document_id, revision: value.revision, path: value.path, location: value.location, updated_at: value.updated_at, file_error: value.file_error, draft: !!value.draft_pending || !value.location || value.text !== value.disk_text }]);
    setClearedRecent(old => old.includes(value.document_id) ? old.filter(id => id !== value.document_id) : old);
  }
  useEffect(() => { localStorage.setItem('inkflow-cleared-recent', JSON.stringify(clearedRecent)); }, [clearedRecent]);
  useEffect(() => {
    if (restored.current) localStorage.setItem('inkflow-open:documents', JSON.stringify({ ids: opened, current: doc?.document_id }));
  }, [opened, doc?.document_id]);
  function chooseDefaultDirectory() { setDirectoryPath(defaultDirectory); setDirectoryError(''); setDirectoryOpen(true); }
  async function saveDefaultDirectory() {
    if (directoryBusy) return;
    setDirectoryBusy(true); setDirectoryError('');
    try {
      const value = await api<{ path: string }>('/local-directory', { method: 'POST', body: JSON.stringify({ path: directoryPath }) });
      localStorage.setItem('inkflow-default-directory', value.path);
      setDefaultDirectory(value.path); setDirectoryOpen(false);
    } catch (error) { setDirectoryError((error as Error).message); }
    finally { setDirectoryBusy(false); }
  }
  async function relocateFile() {
    const current = docRef.current;
    if (!current || busy || inFlight.current) return;
    editor.current?.capture(); inFlight.current = true; setBusy(true); setRelocationError('');
    try {
      const value = await api<Document>(`/documents/${current.document_id}/relocate`, { method: 'POST', body: JSON.stringify({ revision: current.revision, text: textRef.current, path: relocationPath }) });
      install(value); setRelocating(false); setNotice('已关联新位置，原草稿和历史继续保留。');
    } catch (error) { setRelocationError((error as Error).message); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function browseArticle(manual = false) {
    if (busy || browsing || inFlight.current) return;
    editor.current?.capture(); setBrowsing(true); setPickerError('');
    try {
      const value = await api<Document | null>(manual ? '/documents/open' : '/documents/pick-file', { method: 'POST', body: JSON.stringify(manual ? { path: filePath } : {}) });
      if (value) await open(value.document_id);
    } catch (error) { setPickerError((error as Error).message); }
    finally { setBrowsing(false); }
  }
  async function newArticle() {
    if (busy || inFlight.current) return;
    editor.current?.capture(); setBusy(true);
    try {
      const value = await api<Document>('/documents', { method: 'POST', body: '{}' });
      await open(value.document_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function chooseSaveAs() {
    const current = docRef.current;
    setSaveError('');
    setSaveDirectory(current?.location ? parentDirectory(current.location) : defaultDirectory);
    setSaveTarget(current?.location ? fileName(current.path).replace(/(\.[^.]+)$/, '-副本$1') : '新文章.md');
  }
  async function saveFile() {
    const current = docRef.current;
    if (!current || inFlight.current || busy) return;
    if (!current.location) { chooseSaveAs(); return; }
    // 显式保存也先保留最新输入；磁盘冲突或权限失败不丢这次草稿。
    const draft = textRef.current !== current.text ? await operate('edit') : current;
    if (!draft || draft.text !== textRef.current) return;
    const saved = await operate('save');
    if (saved) setNotice('已保存到文件。');
  }
  async function saveAs() {
    const current = docRef.current;
    if (!current || inFlight.current || !saveTarget) return;
    inFlight.current = true; setBusy(true); setSaving('file'); setSaveError('');
    try {
      const value = await api<Document>(`/documents/${current.document_id}/save-as`, { method: 'POST', body: JSON.stringify({ revision: current.revision, text: textRef.current, path: saveDirectory.replace(/[\\/]$/, '') + '/' + saveTarget }) });
      if (value.document_id !== current.document_id) {
        editor.current?.capture();
        sessions.current.set(current.document_id, { doc: current, text: textRef.current, suggestions, selected, preview, processed, auxiliary, scroll: document.querySelector('.document-preview')?.scrollTop ?? 0 });
        setSelected(''); setPreview(false); setProcessed(false); setSuggestions([]);
      }
      install(value); setOpened(old => old.includes(value.document_id) ? old : [...old, value.document_id]); navigate({ document: value.document_id, articleRun: '' });
      setSaveTarget(null); setNotice('已保存到文件，后续编辑：' + value.path);
      await refreshCatalog();
    } catch (e) {
      setSaveError((e as Error).message);
      // 创建失败可能已持久化草稿，刷新 revision 并保留输入。
      try { const value = await api<Document>(`/documents/${current.document_id}`); setDoc(value); docRef.current = value; } catch { /* 保留错误 */ }
    } finally { inFlight.current = false; setBusy(false); setSaving(null); }
  }
  useEffect(() => {
    if (!doc || text === doc.text || busy || saveError) return;
    const timer = setTimeout(() => void operate('edit'), 650);
    return () => clearTimeout(timer);
  }, [text, doc?.revision, busy, saveError]);
  useEffect(() => {
    const run = workflow.run;
    if (!doc || !run || run.editor_source?.document_id !== doc.document_id) { setSuggestions([]); return; }
    const items = run.annotations.map(s => ({ ...s }));
    if (textRef.current !== doc.text && !terminal(run.status)) for (const s of items) if (s.status === 'pending') s.status = 'stale';
    setSuggestions(items);
  }, [workflow.run?.id, workflow.run?.event_cursor, doc?.revision]);
  useEffect(() => {
    if (workflow.run?.pending?.kind === 'diff_review') { setAuxiliary('details'); setAuxiliaryOpen(true); }
  }, [workflow.run?.pending?.id]);
  async function open(path: string) {
    if (path === docRef.current?.document_id) { setOpened(old => old.includes(path) ? old : [...old, path]); setNavigation(false); setOpening(false); return true; }
    if (inFlight.current || busy) return false;
    // 在保存草稿和临时切换编辑权限前固定滚动快照，避免失焦或首帧测量重置滚动。
    editor.current?.capture();
    setSwitchingDocument(true);
    const previous = docRef.current;
    if (previous) {
      const saved = textRef.current !== previous.text ? await operate('edit') : previous;
      if (!saved || saved.text !== textRef.current) { setSwitchingDocument(false); return false; }
      sessions.current.set(previous.document_id, { doc: saved, text: textRef.current, suggestions, selected, preview, processed, auxiliary, scroll: document.querySelector('.document-preview')?.scrollTop ?? 0 });
    }
    const token = ++epoch.current; setBusy(true); setError(''); setNotice(''); setSaveError('');
    try {
      const value = await api<Document>(`/documents/${path}`);
      if (token !== epoch.current) return false;
      const cached = sessions.current.get(path);
      install(value); setOpened(paths => paths.includes(path) ? paths : [...paths, path]);
      setSelected(cached?.selected ?? ''); setPreview(cached?.preview ?? false); setProcessed(cached?.processed ?? false); setAuxiliary(cached?.auxiliary ?? 'suggestions');
      if (readRoute().document !== path) navigate({ document: path, articleRun: '' });
      setNavigation(false); setOpening(false);
      requestAnimationFrame(() => { const pane = document.querySelector('.document-preview'); if (pane && cached) pane.scrollTop = cached.scroll; });
      return true;
    } catch (e) { if (epoch.current === token) { setError((e as Error).message); setPickerError((e as Error).message); } return false; } finally { setBusy(false); setSwitchingDocument(false); }
  }
  useEffect(() => {
    if (!restored.current || busy || !articleVisible) return;
    if (route.document && route.document !== doc?.document_id && restoredDocumentAttempt.current !== route.document) {
      restoredDocumentAttempt.current = route.document;
      if (route.articleRun) rememberRun(route.document, route.articleRun);
      void open(route.document);
    } else if (route.document === doc?.document_id) restoredDocumentAttempt.current = '';
  }, [route.document, articleVisible, busy, doc?.document_id]);
  useEffect(() => {
    if (!articleVisible || !route.articleRun || !doc?.document_id || route.document && route.document !== doc.document_id) return;
    if (workflow.run?.id === route.articleRun) return;
    let live = true;
    rememberRun(doc.document_id, route.articleRun);
    void api<Run>('/runs/' + route.articleRun + '?document_id=' + doc.document_id).then(value => { if (live) { workflow.openRun(value); showAuxiliary('details'); } }).catch(e => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [route.document, route.articleRun, articleVisible, doc?.document_id]);
  async function removeArticle(path: string) {
    if (path === doc?.document_id) {
      editor.current?.capture();
      if (textRef.current !== doc.text && !await operate('edit')) return;
      const next = opened.find(p => p !== path);
      if (next) { if (!await open(next)) return; } else { ++epoch.current; setDoc(null); docRef.current = null; setText(''); setSuggestions([]); navigate({ document: '', articleRun: '' }, true); }
    }
    setOpened(paths => paths.filter(p => p !== path));
    setNotice('已从侧栏移除，文件、草稿、历史和运行仍保留。');
  }
  async function deleteDraft() {
    if (!deletingDraft || deleteBusy || busy || browsing || inFlight.current) return;
    const target = deletingDraft;
    if (opened.includes(target.document_id) || docRef.current?.document_id === target.document_id) { setDeleteError('草稿已重新打开，请先从侧栏移除后再删除。'); return; }
    setDeleteBusy(true); setDeleteError('');
    try {
      await api(`/documents/${target.document_id}`, { method: 'DELETE', body: JSON.stringify({ revision: target.revision }) });
      sessions.current.delete(target.document_id); forgetEditorSession(target.document_id);
      setCatalog(old => old.filter(d => d.document_id !== target.document_id));
      setOpened(old => old.filter(id => id !== target.document_id));
      try { const choices = JSON.parse(localStorage.getItem('inkflow-run-choices:documents') || '{}'); delete choices[target.document_id]; localStorage.setItem('inkflow-run-choices:documents', JSON.stringify(choices)); } catch { /* deleted draft is already removed */ }
      setDeletingDraft(null); setNotice('已删除未命名草稿及编辑版本，已有运行记录仍保留。');
      focusRecentSummary();
    } catch (error) { setDeleteError((error as Error).message); }
    finally { setDeleteBusy(false); }
  }
  function focusRecentSummary() {
    requestAnimationFrame(() => [...document.querySelectorAll<HTMLElement>('.recent-article-list > summary')].find(el => el.getClientRects().length)?.focus());
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
    inFlight.current = true; setBusy(true); setError(''); setNotice(''); setSaveError('');
    setSaving(action === 'save' ? 'file' : 'draft');
    const original = textRef.current, token = epoch.current;
    try {
      if (['accept', 'ignore', 'reject'].includes(action)) {
        await workflow.act(action, { ...extra, editor: { document_id: current.document_id, revision: current.revision, text: original, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id) } });
        const value = await api<Document>(`/documents/${current.document_id}`);
        if (epoch.current === token) { if (textRef.current === original) install(value); else { setDoc(value); docRef.current = value; } }
        return value;
      }
      const value = await api<Document>(`/documents/${current.document_id}/operation`, { method: 'POST', body: JSON.stringify({ revision: current.revision, text: original, action, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id), ...extra }) });
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
      setError((e as Error).message); setSaveError((e as Error).message);
      // 检查失败仍可能已保存快照，更新 revision 并保留本地输入供重试。
      if (action === 'check') try {
        const value = await api<Document>(`/documents/${current.document_id}`);
        if (epoch.current === token) { setDoc(value); docRef.current = value; }
      } catch { /* 保留原始错误 */ }
      return null;
    } finally { inFlight.current = false; setBusy(false); setSaving(null); }
  }
  async function preserveLocalAndRefresh() {
    const current = docRef.current;
    if (!current || inFlight.current) return;
    inFlight.current = true; setBusy(true);
    try {
      const local = await api<Document>('/documents', { method: 'POST', body: '{}' });
      const preserved = await api<Document>(`/documents/${local.document_id}/operation`, { method: 'POST', body: JSON.stringify({ revision: local.revision, text: textRef.current, action: 'edit' }) });
      setOpened(old => [...old, preserved.document_id]);
      await refreshCatalog();
      install(await api<Document>(`/documents/${current.document_id}`));
      setSaveError(''); setError(''); setNotice('此页面的正文已保留为未命名草稿；当前文章已读取服务器最新工作副本。');
    } catch (e) { setError((e as Error).message); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function check(feedbackText = '') {
    if (!docRef.current || checking || inFlight.current) return;
    setChecking(true); setProcessed(false); setError('');
    const path = docRef.current.path, document_id = docRef.current.document_id, token = epoch.current;
    try {
      const old = workflow.active;
      if (old) {
        if (old.workflow_id !== 'full-check' || workflow.run?.id !== old.id || workflow.run.status !== 'waiting') throw new Error('请先完成或停止当前运行，再启动全文检查。');
        await workflow.act('finish_check');
        for (let i = 0; i < 100; i++) {
          const status = await api<Run>(`/runs/${old.id}?document_id=${document_id}`);
          if (terminal(status.status)) break;
          if (i === 99) throw new Error('上一轮尚未结束，请稍后重试。');
          await new Promise(resolve => setTimeout(resolve, 80));
        }
      }
      const saved = await operate('edit');
      if (!saved || epoch.current !== token || textRef.current !== saved.text) return;
      const data = { workflow_id: 'full-check', name: fileName(path), raw_text: saved.text, source_name: path, no_publish: true,
        editor_source: { document_id, revision: saved.revision }, rerun_of: (old ?? (workflow.articleRuns[document_id]?.workflow_id === 'full-check' ? workflow.articleRuns[document_id] : null))?.id, feedback: feedbackText };
      const body = JSON.stringify(data);
      if (startAttempt.current?.body !== body) startAttempt.current = { body, id: requestId() };
      const run = await api<Run>('/runs', { method: 'POST', body: JSON.stringify({ ...data, request_id: startAttempt.current.id }) });
      if (epoch.current === token) { workflow.created(run); navigate({ articleRun: run.id, document: document_id }); showAuxiliary('suggestions'); }
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
    try { setVersions(await api<Version[]>(`/documents/${value.document_id}/versions`)); }
    catch (e) { setError((e as Error).message); }
  }
  async function applyWorkflowResult(snapshot: WorkflowApplyRequest) {
    const current = docRef.current;
    if (!current || inFlight.current || checking) throw new Error('文章正在处理中，请稍后重新对比。');
    if (current.document_id !== snapshot.document_id || current.revision !== snapshot.revision || textRef.current !== snapshot.text) throw new Error('编辑稿已变化，请关闭此对话框并重新对比。');
    inFlight.current = true; setBusy(true);
    try {
      const { runId, ...body } = snapshot;
      const value = await api<Document>(`/runs/${runId}/document-apply?document_id=${snapshot.document_id}`, { method: 'POST', body: JSON.stringify({ ...body, stale_ids: suggestions.filter(s => s.status === 'stale').map(s => s.id) }) });
      if (docRef.current?.document_id === snapshot.document_id) {
        if (textRef.current === snapshot.text) { install(value); setPreview(false); setProcessed(false); }
        else { setDoc(value); docRef.current = value; setSuggestions(items => items.map(s => s.status === 'pending' ? { ...s, status: 'stale' } : s)); }
        setNotice('工作流稿件已载入，替换前草稿保留在历史中。可继续编辑，再保存或提交到工作流。');
      }
    } finally { inFlight.current = false; setBusy(false); setSaving(null); }
  }
  async function workflowAction(action: string, payload: Record<string, unknown> = {}) {
    const current = docRef.current;
    if (['finish', 'publish'].includes(action) && current?.workflow_import?.run_id === workflow.run?.id && current?.workflow_import?.kind === 'article') {
      return workflow.act(action, { ...payload, document: textRef.current });
    }
    if (((action === 'accept' && workflow.run?.pending?.kind === 'diff_review') || action === 'approve_article') && current) {
      const saved = await operate('edit'); if (!saved || textRef.current !== saved.text) throw new Error('正文正在变化，请重新对比。');
      const snapshot = saved.text;
      try {
        await workflow.act(action, { ...payload, editor: { document_id: saved.document_id, revision: saved.revision, text: snapshot } });
      } catch (e) {
        if (action === 'accept' && workflow.run?.pending && (e as Error).message.includes('对比')) setConflict({ runId: workflow.run.id, path: saved.path, text: snapshot, candidate: workflow.run.pending.candidate_text ?? '' });
        throw e;
      }
      const value = await api<Document>(`/documents/${saved.document_id}`);
      if (docRef.current?.document_id === saved.document_id) {
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
    const saved = await operate('edit');
    if (!saved) throw new Error('未能保存编辑快照，请先处理文章错误。');
    if (textRef.current !== draft) throw new Error('提交期间正文已改变，请重新提交。');
    await workflow.act('save', { revision: run.pending.revision, document: draft,
      editor: { document_id: saved.document_id, revision: saved.revision, text: draft } });
    setNotice('编辑稿已提交到工作流，正在保存新的审阅版本。可在节点详情继续确认输出。');
  }
  const pending = suggestions.filter(s => ['pending', 'stale'].includes(s.status));
  const done = suggestions.filter(s => ['accepted', 'rejected', 'ignored', 'reviewed'].includes(s.status));
  const visible = processed ? done : pending;
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
    <div className="editor-brand"><span>文章</span><IconButton aria-label="收起文件导航" onClick={() => mobileFiles ? setNavigation(false) : setFilesOpen(false)}><Close /></IconButton></div>
    <div className="sidebar-file-actions"><Button disabled={busy || browsing} onClick={() => void newArticle()}>新建文章</Button><Button startIcon={<FolderOutlined />} disabled={busy || browsing} onClick={() => { setPickerError(''); setOpening(true); }}>打开文章</Button></div>
    <label className="file-search"><Search fontSize="small" /><input ref={searchInput} type="search" placeholder="搜索文件名或路径" aria-label="搜索文章" value={query} onChange={e => setQuery(e.target.value)} /><kbd>Ctrl K</kbd></label>
    <div className="file-list-heading"><span>文章路径</span><span>{opened.length}</span><IconButton size="small" aria-label="刷新文章状态" disabled={busy || browsing} onClick={() => void loadFiles()}><Refresh fontSize="small" /></IconButton></div>
    <OpenArticles documents={opened.map(id => summaries.find(d => d.document_id === id)).filter((d): d is DocumentSummary => !!d)} current={doc?.document_id} query={query} runs={workflow.articleRuns} busy={busy || browsing} onOpen={p => void open(p)} onRemove={p => void removeArticle(p)} />
    <details className="recent-article-list"><summary><ChevronRight className="disclosure-chevron" />最近文章与移除的草稿</summary><div className="file-list">{recentArticles.map(d => {
      const name = articleName(d);
      return <div key={d.document_id} className="opened-article"><ArticleFileButton doc={d} run={workflow.articleRuns[d.document_id]} busy={busy || browsing || deleteBusy} onOpen={id => void open(id)} /><Tooltip title={d.location ? '清除本浏览器的最近记录，保留文件、草稿和历史；可重新打开' : '永久删除未命名草稿及编辑版本'}><span><IconButton size="small" disabled={busy || browsing || deleteBusy} aria-label={`${d.location ? '清除最近记录' : '删除草稿'} ${name}`} onClick={() => {
        if (d.location) { setClearedRecent(old => [...new Set([...old, d.document_id])]); setNotice('已清除最近记录，文件、草稿和历史仍保留，可通过打开文章找回。'); focusRecentSummary(); }
        else { setDeletingDraft(d); setDeleteError(''); }
      }}>{d.location ? <Close fontSize="small" /> : <DeleteOutline fontSize="small" />}</IconButton></span></Tooltip></div>;
    })}{!recentArticles.length && <p className="editor-empty">{query.trim() ? '没有匹配记录。' : '暂无最近文章或移除的草稿。'}</p>}</div></details>
    <div className="default-directory"><Tooltip describeChild disableInteractive placement="right" title={<span className="article-file-details">{defaultDirectory || '尚未选择默认保存目录'}</span>}><button aria-label="修改默认保存目录" disabled={busy || browsing} onClick={chooseDefaultDirectory}><FolderOutlined fontSize="small" /><span>默认保存目录</span></button></Tooltip></div>
  </aside>;

  return <div className="application-shell">
    <header className="application-navigation"><strong>InkFlow</strong><nav aria-label="全局导航">{([['articles', '文章'], ['workflows', '工作流'], ['tasks', '任务'], ['settings', '设置']] as const).map(([page, label]) => <Button key={page} component="a" href={'?page=' + page} aria-current={route.page === page ? 'page' : undefined} onClick={event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); go(page); }}>{label}</Button>)}</nav></header>
    <div className="application-content">
    <div data-page="articles" hidden={!articleVisible} className={`editor-workbench ${!filesOpen ? 'files-collapsed' : ''}`}>
    <a href="#article-content" className="skip-link">跳到文章内容</a>
    {!mobileFiles && filesOpen && navigationPanel}
    <Drawer open={mobileFiles && navigation} onClose={() => setNavigation(false)} slotProps={{ paper: { className: 'files-drawer' }, transition: { onEntered: () => { if (!opening && !directoryOpen && document.activeElement?.classList.contains('files-drawer')) searchInput.current?.focus(); } } }}>{navigationPanel}</Drawer>
    <div className="article-main">
    {reviewOrigin.current && <div className="review-return"><Button onClick={() => { const url = reviewOrigin.current!; reviewOrigin.current = null; sessionStorage.removeItem('inkflow-review-origin'); window.history.pushState(null, '', url || '?page=tasks'); window.dispatchEvent(new Event('inkflow-navigation')); }}>返回来源页面</Button></div>}
      <header className="article-header">
        <IconButton aria-label={mobileFiles || !filesOpen ? '打开文件导航' : '收起文件导航'} aria-expanded={mobileFiles ? navigation : filesOpen} onClick={() => mobileFiles ? setNavigation(true) : setFilesOpen(value => !value)}><Menu fontSize="small" /></IconButton>
        <div className="article-breadcrumb" data-page-heading tabIndex={-1}><ArticleOutlined fontSize="small" /><span>文章</span><ChevronRight fontSize="small" /><button className="document-location" disabled={!doc} title={doc?.path} onClick={() => setLocationOpen(true)}>{doc ? fileName(doc.path) : '选择一篇文章'}</button></div>
        <div className="article-header-actions">
          <Button className="auxiliary-toggle" aria-expanded={auxiliaryOpen} aria-controls="workbench-auxiliary" onClick={() => setAuxiliaryOpen(value => !value)}>{auxiliaryOpen ? '收起辅助窗格' : '展开辅助窗格'}</Button>
          <span role="status" title="刷新恢复已持久化的文章草稿与打开列表；撤销重做仅保留在本次页面会话。" className={`save-state ${dirty ? 'is-dirty' : ''}`}>{saveLabel}</span>
          <details className="article-more"><summary><ChevronRight className="disclosure-chevron" />更多</summary><Tooltip title="文章历史"><span><Button className="history-button" startIcon={<HistoryOutlined />} disabled={!doc || busy} onClick={() => void history()}>历史</Button></span></Tooltip><Button disabled={!doc || busy || browsing} onClick={chooseSaveAs}>另存为</Button></details>
          <Button variant="contained" disabled={!doc || busy || browsing} onClick={() => void saveFile()}>保存文件</Button>
        </div>
        {busy && <LinearProgress className="editor-progress" aria-label="操作进行中" />}
      </header>
      {doc?.file_error && <Alert severity="warning" action={<div className="button-row"><Button disabled={busy || browsing} onClick={() => { setRelocationPath(doc.location || ''); setRelocationError(''); setRelocating(true); }}>重新定位文件</Button><Button disabled={busy || browsing} onClick={chooseSaveAs}>另存为</Button></div>}>{doc.file_error}</Alert>}
      {doc?.disk_conflict && <Alert severity="warning">文件已被外部修改，当前草稿仍保留。请读取磁盘版本核对。</Alert>}
      {!!doc?.migration_conflicts?.length && <Alert severity="warning" action={<Button onClick={() => void history()}>查看冲突历史</Button>}>同一文件存在不同的旧草稿，已全部保留。请在历史中核对迁移冲突版本。</Alert>}
      <WorkflowControls controller={{ ...workflow, openRun: run => { workflow.openRun(run); navigate({ articleRun: run.id, document: run.editor_source?.document_id ?? '' }); } }} onStart={() => void startWorkflow()} onPending={() => { workflow.setSelected(workflow.run?.pending?.node ?? null); showAuxiliary(workflow.run?.pending?.kind === 'suggestion_review' ? 'suggestions' : 'details'); }} />
      <WorkflowDocumentBridge run={workflow.run && !terminal(workflow.run.status) && workflow.run.document_result?.kind !== 'redaction' ? workflow.run : null} target={doc} text={text} busy={busy || checking || workflow.busy} onOpen={path => void open(path)} onChoose={() => mobileFiles ? setNavigation(true) : setFilesOpen(true)} onApply={applyWorkflowResult} onSubmit={submitWorkflowDocument} />
      {workflow.error && <Alert severity="error" className="editor-alert" action={<Button onClick={workflow.retry}>重试</Button>} onClose={() => workflow.setError('')}>{workflow.error}</Alert>}
      {error && <Alert severity="error" className="editor-alert" action={<Button disabled={busy} onClick={() => {
        if (!doc) void loadFiles();
        else if (window.confirm('读取磁盘文件替换编辑区？当前草稿将保留在历史中。')) void operate('reload');
      }}>{doc ? '读取磁盘版本' : '重试'}</Button>} onClose={() => setError('')}>{error}</Alert>}
      {saveError && doc && <Alert severity="warning" action={<Button disabled={busy} onClick={() => void preserveLocalAndRefresh()}>保留本地稿并读取最新</Button>}>此页面的输入仍在编辑区。可先保留为独立草稿，再读取最新工作副本。</Alert>}
      {notice && <Alert severity="info" className="editor-alert" onClose={() => setNotice('')}>{notice}</Alert>}
      <div ref={columns} className={`article-columns ${!auxiliaryOpen ? 'auxiliary-collapsed' : ''}`} style={{ '--auxiliary-width': `${paneWidth}px`, '--auxiliary-height': `${paneHeight}px` } as CSSProperties}>
        <main id="article-content" tabIndex={-1} className="article-body" aria-label="文章编辑区">
          <div className="article-toolbar">
            <div className="editor-segmented" role="group" aria-label="文章视图"><button disabled={!doc} aria-pressed={!preview} onClick={() => setPreview(false)}>编辑</button><button disabled={!doc} aria-pressed={preview} onClick={() => setPreview(true)}>预览</button></div>
            <span className="toolbar-spacer" />
            <Tooltip title="撤销"><span><IconButton aria-label="撤销" disabled={!doc || preview || busy} onClick={() => editor.current?.undo()}><Undo fontSize="small" /></IconButton></span></Tooltip>
            <Tooltip title="重做"><span><IconButton aria-label="重做" disabled={!doc || preview || busy} onClick={() => editor.current?.redo()}><Redo fontSize="small" /></IconButton></span></Tooltip>
          </div>
          {!doc ? <div className="article-paper editor-empty large"><ArticleOutlined /><h1>{busy ? '正在读取文章' : '从一篇文章开始'}</h1><Button disabled={busy} onClick={() => void newArticle()}>新建文章</Button><p>打开本地 Markdown 或 MDX，<br />专注写作，逐条打磨。</p><Button variant="outlined" disabled={busy || browsing} startIcon={<FolderOutlined />} onClick={() => { setPickerError(''); setOpening(true); }}>选择文章</Button></div> : <>
            <div className="article-paper" hidden={preview}><ArticleEditor key={doc.document_id} documentKey={doc.document_id} text={text} suggestions={editableRun ? suggestions : []} selected={selected} readOnly={switchingDocument || browsing || busy && !checking && saving !== 'draft'} onChange={changed} onSelect={id => { setSelected(id); setProcessed(false); showAuxiliary('suggestions'); scrollSuggestion(id); }} handle={editor} /></div>
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
          <div className="auxiliary-tabs" role="tablist" aria-label="辅助内容">{([['suggestions', '建议与待办'], ['details', '当前待办'], ['runs', '本文运行'], ['graph', '流程图']] as const).map(([id, label]) => <button id={`auxiliary-tab-${id}`} key={id} role="tab" aria-selected={auxiliary === id} aria-controls={`auxiliary-panel-${id}`} tabIndex={auxiliary === id ? 0 : -1} onClick={() => setAuxiliary(id)} onKeyDown={e => {
            const ids: AuxiliaryView[] = ['suggestions', 'details', 'runs', 'graph'];
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
            e.preventDefault(); const next = e.key === 'Home' ? ids[0] : e.key === 'End' ? ids[3] : ids[(ids.indexOf(id) + (e.key === 'ArrowRight' ? 1 : 3)) % 4];
            setAuxiliary(next); document.getElementById(`auxiliary-tab-${next}`)?.focus();
          }}>{label}{id === 'suggestions' && pending.length > 0 ? ` ${pending.length}` : ''}{id === 'details' && workflow.run?.pending ? ' · 待办' : ''}</button>)}</div>
        <section id="auxiliary-panel-suggestions" role="tabpanel" aria-labelledby="auxiliary-tab-suggestions" className="article-suggestions" hidden={auxiliary !== 'suggestions'}>
          <div className="suggestions-heading"><h2>{workflow.run && workflow.run.workflow_id !== "full-check" ? "脱敏审核" : "全文检查"} <span>{pending.length}</span></h2></div>
          {workflow.run && <Button size="small" onClick={() => { setVersions([{ id: workflow.run!.id, created_at: workflow.run!.created_at, operation: '检查输入快照', text: workflow.run!.annotation_snapshot }]); setVersion(null); }}>查看检查快照</Button>}
          <div className="suggestion-tabs" role="group" aria-label="建议状态"><button aria-pressed={!processed} onClick={() => setProcessed(false)}>待处理 <span>{pending.length}</span></button><button aria-pressed={processed} onClick={() => setProcessed(true)}>已处理 <span>{done.length}</span></button></div>
          <div className="suggestion-list" aria-busy={checking || workflow.run?.workflow_id === 'full-check' && workflow.run.status === 'running'}>
            {workflow.run?.state.failure && <Alert severity="error">{workflow.run.state.failure}</Alert>}
            {workflow.run && ['recovering', 'verification_required', 'recovery_blocked'].includes(workflow.run.status) && <Alert severity={workflow.run.status === 'recovering' ? 'info' : 'warning'}>{workflow.run.recovery_message || '正在恢复运行。'} 请在节点详情查看处理方式。</Alert>}
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
                    {s.status === 'stale' && <div className="suggestion-stale"><span>原文已修改，请从“本文运行”重新检查后再采用。</span></div>}
                  </>}
                </div>
                {!expanded && s.status !== 'pending' && <span className="collapsed-state">{statuses[s.status] || s.status}</span>}
              </section>;
            })}
          </div>
          {workflow.run?.pending && workflow.run.pending.kind !== 'suggestion_review' && auxiliary === 'suggestions' && <NodeDetails workflow={workflow.run.workflow} run={workflow.run} selected={workflow.run.pending?.node ?? workflow.run.current_node} busy={workflow.busy || busy} onAction={workflowAction} onSelect={workflow.setSelected} articleText={text} canSaveProperties={!!doc && text === doc.text && doc.revision === workflow.run?.editor_base?.revision} />}
          {workflow.run?.pending?.kind === 'suggestion_review' && <Button disabled={busy || workflow.busy} onClick={() => void workflowAction('finish_check').catch(e => setError(e.message))}>结束本轮检查</Button>}
          <footer className="review-footer" role="status">{workflow.run ? `${workflow.run.workflow.name} · ${workflow.run.status === 'waiting' ? '等待人工处理' : ({ recovering: '恢复中', verification_required: '等待核验', recovery_blocked: '恢复受阻', running: '运行中', completed: '已完成', failed: '失败', stopped: '已停止', interrupted: '已中断', stopping: '停止中' } as Record<string, string>)[workflow.run.status] ?? workflow.run.status}` : doc?.check ? ({ running: '检查中', failed: '检查失败 · 可重试', interrupted: '检查已中断 · 请重试', completed: '检查完成 · 修改由你确认' }[doc.check.status] || doc.check.status) : '建议供参考，保留你的表达。'}</footer>
        </section>
        <WorkflowPanel controller={{ ...workflow, act: workflowAction }} view={auxiliary} onView={setAuxiliary} articleText={text} onOpenDetails={run => navigate({ page: 'tasks', tab: 'runs', run: run.id, runDocument: run.editor_source?.document_id ?? '' })} canSaveProperties={!!doc && text === doc.text && doc.revision === workflow.run?.editor_base?.revision} />
        </aside>
      </div>
    </div>
    <section hidden={!locationOpen} className="document-location-panel" aria-label="文件位置"><TextField fullWidth multiline label="完整路径" value={doc?.location || '未命名文章 · 尚未选择位置'} slotProps={{ input: { readOnly: true } }} /><div className="button-row"><Button disabled={!doc?.location} onClick={() => { void navigator.clipboard.writeText(doc!.location!).then(() => setNotice('完整路径已复制。')).catch(() => setError('无法访问剪贴板，请在路径框内手动复制。')); }}>复制完整路径</Button><Button onClick={() => setLocationOpen(false)}>收起位置</Button></div></section>
    <Dialog open={saveTarget !== null} onClose={busy ? undefined : () => setSaveTarget(null)} fullWidth aria-labelledby="save-as-title"><DialogTitle id="save-as-title">{doc?.location ? '另存为新文件' : '首次保存到文件'}</DialogTitle><DialogContent><div className="form-stack">{saveError && <Alert severity="error">{saveError}</Alert>}<DirectoryField label="目标目录" value={saveDirectory} purpose="workspace" disabled={busy} onChange={setSaveDirectory} onError={setSaveError} /><TextField autoFocus label="文件名（Markdown / MDX）" value={saveTarget ?? ''} disabled={busy} onChange={e => setSaveTarget(e.target.value)} /><p>保存成功后继续编辑目标文件。已有同名文件不会被覆盖。</p></div></DialogContent><DialogActions><Button disabled={busy} onClick={() => setSaveTarget(null)}>取消</Button><Button variant="contained" disabled={busy || !saveTarget?.trim() || /[\\/]/.test(saveTarget)} onClick={() => void saveAs()}>保存到此位置</Button></DialogActions></Dialog>
    <Dialog open={!!conflict} onClose={() => setConflict(null)} fullWidth maxWidth="lg" aria-labelledby="redaction-conflict-title"><DialogTitle id="redaction-conflict-title">脱敏候选与当前稿件冲突</DialogTitle><DialogContent><p>工作副本已在检查后修改。核对两份正文，确认替换会保留当前稿件的历史版本。</p>{conflict && <DocumentDiff before={conflict.text} after={conflict.candidate} beforeLabel="当前工作副本" afterLabel="候选脱敏稿" initialLayout="split" expandable={false} />}{conflict?.text !== text && <Alert severity="warning">正文再次变化，请重新对比后确认。</Alert>}</DialogContent><DialogActions><Button onClick={() => setConflict(null)}>返回重新改写</Button><Button onClick={() => setConflict(old => old ? { ...old, text } : null)}>重新对比当前稿</Button><Button variant="contained" disabled={busy || workflow.busy || conflict?.text !== text || conflict?.path !== doc?.path || conflict?.runId !== workflow.run?.id} onClick={() => void workflowAction('accept', { confirm_conflict: true }).then(() => setConflict(null)).catch(e => setError(e.message))}>确认替换并保留历史</Button></DialogActions></Dialog>
    <Dialog open={opening} onClose={browsing ? undefined : () => setOpening(false)} fullWidth aria-labelledby="open-article-title"><DialogTitle id="open-article-title">打开文章</DialogTitle><DialogContent>
      <div className="article-picker-actions"><Button variant="outlined" startIcon={<FolderOpenOutlined />} disabled={busy || browsing} onClick={() => void browseArticle()}>{browsing ? '正在选择文件…' : '浏览文件…'}</Button></div>
      {pickerError && <Alert severity="error" sx={{ mb: 2 }}>{pickerError}</Alert>}
      <TextField fullWidth label="文件完整路径" value={filePath} onChange={e => setFilePath(e.target.value)} helperText="打开任意位置的 Markdown / MDX 文件，按路径加入侧栏" /><Button disabled={busy || browsing || !filePath.trim()} onClick={() => void browseArticle(true)}>打开此文件</Button>
</DialogContent><DialogActions><Button disabled={browsing} onClick={() => setOpening(false)}>关闭</Button></DialogActions></Dialog>
    <Dialog open={directoryOpen} onClose={directoryBusy ? undefined : () => setDirectoryOpen(false)} fullWidth aria-labelledby="directory-title"><DialogTitle id="directory-title">默认保存目录</DialogTitle><DialogContent><div className="form-stack">
      {directoryError && <Alert severity="error">{directoryError}</Alert>}
      <DirectoryField label="默认保存目录" value={directoryPath} purpose="workspace" disabled={directoryBusy} onPicking={setDirectoryBusy} onChange={setDirectoryPath} onError={setDirectoryError} />
      <p>新建文章首次保存时默认使用此目录，已有文章和草稿的位置保持原样。</p>
    </div></DialogContent><DialogActions><Button disabled={directoryBusy} onClick={() => setDirectoryOpen(false)}>取消</Button><Button variant="contained" disabled={directoryBusy || !directoryPath.trim()} onClick={() => void saveDefaultDirectory()}>使用此目录</Button></DialogActions></Dialog>
    <Dialog open={relocating} onClose={busy ? undefined : () => setRelocating(false)} fullWidth aria-labelledby="relocate-title"><DialogTitle id="relocate-title">重新定位文件</DialogTitle><DialogContent><div className="form-stack">
      {relocationError && <Alert severity="error">{relocationError}</Alert>}
      <TextField autoFocus label="文件现在的完整路径" value={relocationPath} disabled={busy} onChange={e => setRelocationPath(e.target.value)} />
      <p>关联移动后的原文件，保留文章草稿、历史和运行；文件内容需要与原磁盘版本一致。</p>
    </div></DialogContent><DialogActions><Button disabled={busy} onClick={() => setRelocating(false)}>取消</Button><Button variant="contained" disabled={busy || !relocationPath.trim()} onClick={() => void relocateFile()}>关联此文件</Button></DialogActions></Dialog>
    {newRun && workflow.workflows.find(w => w.id === workflow.workflowId) && <NewRunDialog open workflow={workflow.workflows.find(w => w.id === workflow.workflowId)!} currentDocument={doc ? { document_id: doc.document_id, path: doc.path, revision: doc.revision, text } : undefined} onClose={() => setNewRun(false)} onCreated={value => { workflow.created(value); navigate({ articleRun: value.id, document: value.editor_source?.document_id ?? '' }); setNewRun(false); showAuxiliary('suggestions'); }} />}
    <Dialog open={!!rejecting} onClose={() => !busy && setRejecting(null)} fullWidth aria-labelledby="feedback-title"><DialogTitle id="feedback-title">拒绝并反馈</DialogTitle><DialogContent><div className="feedback-form"><p>说明你希望保留或调整的内容，重新检查时会参考这份反馈。</p><TextField autoFocus multiline minRows={4} label="重新生成的要求" value={feedback} onChange={e => setFeedback(e.target.value)} helperText={`${feedback.length} / 3000`} slotProps={{ htmlInput: { maxLength: 3000 } }} /></div></DialogContent><DialogActions><Button disabled={busy} onClick={() => setRejecting(null)}>取消</Button><Button variant="contained" disabled={busy || !feedback.trim()} onClick={() => {
      const s = rejecting!;
      void operate('reject', { suggestion_id: s.id, feedback }).then(async v => {
        if (v) { setRejecting(null); await check(`用户拒绝了建议「${s.title.slice(0, 200)}」。\n要求：${feedback}`); }
      });
    }}>拒绝并重新检查</Button></DialogActions></Dialog>
    <Drawer anchor="right" open={versions !== null} onClose={() => { setVersions(null); setVersion(null); }} slotProps={{ paper: { className: "article-history-panel" } }}><DialogTitle id="versions-title">文章历史</DialogTitle><DialogContent><p className="version-description">选择一个版本查看内容，恢复后可继续编辑，再手动保存到文件。</p><div className="version-layout"><div className="version-list">{versions?.map(v => <button key={v.id} aria-pressed={version?.id === v.id} onClick={() => setVersion(v)}><strong>{operations[v.operation] || v.operation}</strong><time>{formatTime(v.created_at)}</time><small>{v.id.slice(0, 8)}</small></button>)}</div><pre>{version ? version.text || '（空文档）' : '选择版本查看原文'}</pre></div></DialogContent><DialogActions><Button onClick={() => { setVersions(null); setVersion(null); }}>关闭</Button><Button variant="contained" disabled={!version || busy || version.operation === "检查输入快照"} onClick={() => {
      if (dirty && !window.confirm('恢复版本会替换当前未保存正文，当前内容已记录在历史中。继续？')) return;
      void operate('restore', { version: version!.id }).then(v => { if (v) { setVersions(null); setVersion(null); setNotice('已恢复到编辑区，可复查后保存文件。'); } });
    }}>恢复到编辑区</Button></DialogActions></Drawer>
    </div>
    <Dialog open={!!deletingDraft} onClose={() => !deleteBusy && setDeletingDraft(null)} fullWidth maxWidth="xs" aria-labelledby="delete-draft-title" aria-describedby="delete-draft-description">
      <DialogTitle id="delete-draft-title">删除未命名草稿？</DialogTitle>
      <DialogContent><p id="delete-draft-description">将永久删除“未命名文章 · {deletingDraft?.document_id.slice(0, 6)}”的正文和编辑版本，无法恢复。已有运行记录及其输入快照仍保留。</p>{deleteError && <Alert severity="error">{deleteError}</Alert>}</DialogContent>
      <DialogActions><Button autoFocus disabled={deleteBusy} onClick={() => setDeletingDraft(null)}>取消</Button><Button color="error" variant="contained" disabled={deleteBusy || busy || browsing} onClick={() => void deleteDraft()}>{deleteBusy ? '删除中…' : '删除草稿'}</Button></DialogActions>
    </Dialog>
    <section data-page="workflows" hidden={!studioOpen} className="application-page"><WorkflowStudio open={studioOpen} target={studioTarget} /></section>
    <section data-page="tasks" hidden={route.page !== 'tasks'} className="application-page">
      <h1 data-page-heading tabIndex={-1}>任务</h1><div className="page-tabs" role="group" aria-label="任务视图"><Button aria-pressed={runCenterOpen} onClick={() => navigate({ tab: 'runs' })}>运行记录</Button><Button aria-pressed={automationOpen} onClick={() => navigate({ tab: 'watch' })}>持续关注</Button></div>
      <div hidden={!runCenterOpen}><RunCenter open={runCenterOpen} onOpenArticle={reviewRun} /></div>
      <div hidden={!automationOpen}><AutomationCenter open={automationOpen} onOpenDocument={async id => reviewDocument(id)} onOpenRun={reviewRun} /></div>
    </section>
    <section data-page="settings" hidden={route.page !== 'settings'} className="application-page"><SettingsPage /></section>
    </div>
  </div>;
}
