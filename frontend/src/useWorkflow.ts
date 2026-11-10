import { useCallback, useEffect, useRef, useState } from 'react';
import { api, requestId } from './api';
import { terminal, type Run, type RunSummary, type Workflow, type WorkflowSummary } from './types';

export function useWorkflow(path: string | null, workspace: string) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [workflowId, setWorkflowId] = useState('full-check');
  const [definition, setDefinition] = useState<Workflow | null>(null);
  const [history, setHistory] = useState<RunSummary[]>([]), [legacy, setLegacy] = useState(false);
  const [historyMore, setHistoryMore] = useState(false);
  const [articleRuns, setArticleRuns] = useState<Record<string, RunSummary>>({});
  const [runId, setRunId] = useState<string | null>(null), [run, setRun] = useState<Run | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'offline'>('connecting');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [reconnect, setReconnect] = useState(0);
  const choices = useRef<Record<string, string>>({}), nodes = useRef<Record<string, string | null>>({});
  const pendingSeen = useRef<Record<string, string>>({});
  const context = `${workspace}|${path ?? ''}`;
  const current = useRef({ context, runId }); current.current = { context, runId };
  const actionAttempt = useRef<{ signature: string; id: string } | null>(null);
  const submitting = useRef(false);
  const listUrl = legacy ? '/runs?unlinked=true' : path ? `/runs?path=${encodeURIComponent(path)}` : '/runs?workspace=true';
  const loadHistory = useCallback(async () => {
    if (!path && !legacy) { setHistory([]); setHistoryMore(false); return; }
    const rows = await api<RunSummary[]>(listUrl);
    if (current.current.context === context) { setHistory(rows); setHistoryMore(rows.length === 50); }
  }, [listUrl, context, path, legacy]);
  const loadRun = useCallback(async (id: string) => {
    const value = await api<Run>(`/runs/${id}`);
    if (current.current.context === context && current.current.runId === id) {
      setRun(old => old?.id === id && old.event_cursor > value.event_cursor ? old : value);
      setSelected(old => old ?? nodes.current[id] ?? value.pending?.node ?? value.current_node);
    }
    return value;
  }, [context]);
  const refreshArticles = useCallback(async () => {
    const value = await api<Record<string, RunSummary>>('/article-runs');
    setArticleRuns(value);
    return value;
  }, []);
  useEffect(() => {
    let disposed = false;
    const poll = async () => { try { if (!disposed) await refreshArticles(); } catch { /* SSE/explicit requests expose connection errors */ } };
    void poll(); const timer = setInterval(() => void poll(), 1200);
    return () => { disposed = true; clearInterval(timer); };
  }, [refreshArticles]);
  useEffect(() => {
    try { choices.current = JSON.parse(localStorage.getItem(`inkflow-run-choices:${workspace}`) || '{}'); } catch { choices.current = {}; }
    setLegacy(false); setRun(null); setSelected(null); setRunId(null); setHistory([]);
    let disposed = false;
    if (path) void refreshArticles().then(all => {
      if (disposed) return;
      const id = choices.current[context] ?? all[path]?.id ?? null;
      current.current.runId = id; setRunId(id);
    }).catch(err => { if (!disposed) setError((err as Error).message); });
    return () => { disposed = true; };
  }, [context, path, workspace, refreshArticles]);
  useEffect(() => { void loadHistory().catch(err => setError((err as Error).message)); }, [loadHistory, articleRuns[path ?? '']?.updated_at]);
  useEffect(() => {
    if (run?.pending && pendingSeen.current[run.id] !== run.pending.id) {
      pendingSeen.current[run.id] = run.pending.id;
      nodes.current[run.id] = run.pending.node;
      setSelected(run.pending.node);
    }
  }, [run?.id, run?.pending?.id]);
  useEffect(() => { void api<WorkflowSummary[]>('/workflows').then(setWorkflows).catch(err => setError((err as Error).message)); }, [reconnect]);
  useEffect(() => {
    let disposed = false;
    void api<Workflow>(`/workflows/${workflowId}`).then(value => { if (!disposed) setDefinition(value); }).catch(err => { if (!disposed) setError((err as Error).message); });
    return () => { disposed = true; };
  }, [workflowId]);
  useEffect(() => {
    if (!runId) { setConnection('connected'); return; }
    let disposed = false, source: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (timer) return;
      timer = setTimeout(() => { timer = undefined; if (!disposed) void loadRun(runId).catch(err => setError((err as Error).message)); }, 80);
    };
    const connect = async () => {
      source?.close(); setConnection('connecting');
      try {
        const value = await loadRun(runId);
        if (disposed) return;
        if (terminal(value.status)) { setConnection('connected'); return; }
        source = new EventSource(`/api/runs/${runId}/events?after=${value.event_cursor}`);
        source.onopen = () => { if (!disposed) { setConnection('connected'); refresh(); } };
        source.onerror = () => { if (!disposed) setConnection('offline'); };
        source.addEventListener('run_event', refresh);
        source.addEventListener('end', () => { source?.close(); refresh(); void refreshArticles(); });
      } catch (err) { if (!disposed) { setError((err as Error).message); setConnection('offline'); } }
    };
    const offline = () => { source?.close(); setConnection('offline'); };
    const online = () => void connect();
    window.addEventListener('offline', offline); window.addEventListener('online', online); void connect();
    return () => { disposed = true; source?.close(); if (timer) clearTimeout(timer); window.removeEventListener('offline', offline); window.removeEventListener('online', online); };
  }, [runId, loadRun, reconnect, refreshArticles]);
  function openRun(value: RunSummary) {
    if (value.id !== runId) { setRun(null); setSelected(nodes.current[value.id] ?? null); }
    if (value.editor_source) { choices.current[context] = value.id; localStorage.setItem(`inkflow-run-choices:${workspace}`, JSON.stringify(choices.current)); }
    current.current.runId = value.id; setRunId(value.id);
  }
  async function act(action: string, payload: Record<string, unknown> = {}) {
    if (!run || submitting.current) return;
    submitting.current = true; setBusy(true);
    const id = run.id, data = { pending_id: run.pending?.id ?? null, action, payload };
    const signature = JSON.stringify([id, data]);
    if (actionAttempt.current?.signature !== signature) actionAttempt.current = { signature, id: requestId() };
    try {
      await api(`/runs/${id}/actions`, { method: 'POST', body: JSON.stringify({ ...data, request_id: actionAttempt.current.id }) });
      await loadRun(id); await refreshArticles(); await loadHistory();
    } catch (err) { void loadRun(id).catch(() => undefined); throw err; }
    finally { submitting.current = false; setBusy(false); }
  }
  function created(value: Run) { setLegacy(false); openRun(value); setRun(value); void refreshArticles(); void loadHistory(); }
  async function more() {
    try { const rows = await api<RunSummary[]>(`${listUrl}&offset=${history.length}`); if (current.current.context === context) { setHistory(old => [...old, ...rows]); setHistoryMore(rows.length === 50); } }
    catch (err) { setError((err as Error).message); }
  }
  const summary = articleRuns[path ?? ''];
  const active = summary && !terminal(summary.status) ? summary : null;
  const visibleRun = run && (legacy && !run.editor_source || run.editor_source?.workspace === workspace && run.editor_source?.path === path) ? run : null;
  return { workflows, workflowId, setWorkflowId, workflow: visibleRun?.workflow ?? definition, history, historyMore, more,
    run: visibleRun, selected, setSelected: (id: string | null) => { if (runId) nodes.current[runId] = id; setSelected(id); },
    connection, error, setError, busy, openRun, act, created, refresh: () => runId ? loadRun(runId) : Promise.resolve(null),
    active, articleRuns, legacy, setLegacy: (value: boolean) => { setLegacy(value); if (!value && summary) openRun(summary); }, hasArticle: !!path,
    retry: () => { void refreshArticles(); void loadHistory(); setReconnect(value => value + 1); } };
}
export type WorkflowController = ReturnType<typeof useWorkflow>;
