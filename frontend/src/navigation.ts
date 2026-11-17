import { useSyncExternalStore } from 'react';

export type Page = 'articles' | 'workflows' | 'tasks' | 'settings';
export type Route = { page: Page; tab: string; recordScope: string; workflow: string; stage: string; run: string; runDocument: string; articleRun: string; document: string; task: string; ai: string; step: string; proposal: string };
const event = 'inkflow-navigation';
const subscribe = (notify: () => void) => {
  window.addEventListener('popstate', notify);
  window.addEventListener(event, notify);
  return () => { window.removeEventListener('popstate', notify); window.removeEventListener(event, notify); };
};
export function readRoute(): Route {
  const p = new URLSearchParams(window.location.search);
  const page = p.get('page');
  return { page: ['workflows', 'tasks', 'settings'].includes(page ?? '') ? page as Page : 'articles',
    tab: p.get('tab') === 'watch' ? 'watch' : 'runs', recordScope: p.get('recordScope') === 'legacy' ? 'legacy' : 'documents', workflow: p.get('workflow') ?? 'full-check',
    stage: ['test', 'versions'].includes(p.get('stage') ?? '') ? p.get('stage')! : 'design',
    run: p.get('run') ?? '', runDocument: p.get('runDocument') ?? '', articleRun: p.get('articleRun') ?? '', document: p.get('document') ?? '', task: p.get('task') ?? '',
    ai: p.get('ai') ?? '', step: p.get('step') ?? '0', proposal: p.get('proposal') ?? '' };
}
export function navigate(patch: Partial<Route>, replace = false) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(patch)) { if (value) url.searchParams.set(key, value); else url.searchParams.delete(key); }
  if (url.href === window.location.href) return;
  window.history[replace ? 'replaceState' : 'pushState'](window.history.state, '', url);
  window.dispatchEvent(new Event(event));
}
export function useRoute() {
  useSyncExternalStore(subscribe, () => window.location.search);
  return readRoute();
}
export type AIRepair = { run_id: string; node_id: string; attempt_id: string };
export function openAI(repair?: AIRepair, documentId?: string) {
  sessionStorage.setItem('inkflow-ai-origin', window.location.search);
  sessionStorage.setItem('inkflow-ai-repair', JSON.stringify({ repair, documentId }));
  navigate({ page: 'workflows', ai: repair ? 'repair' : 'create', step: '0', proposal: '' });
}
export function returnFromAI() {
  const origin = sessionStorage.getItem('inkflow-ai-origin');
  window.history.pushState(null, '', origin || '?page=workflows');
  window.dispatchEvent(new Event(event));
}
