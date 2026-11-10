import { useEffect, useRef } from 'react';
import { Compartment, EditorState, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView, keymap, lineNumbers, WidgetType, type DecorationSet } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, isolateHistory, undo, redo } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import type { Suggestion } from './EditorWorkbench';

const externalFlags = new Map<string, { value: boolean }>();
const editCompartments = new Map<string, Compartment>();
// 缓存 EditorState 的扩展闭包也会保留；复用可更新载体，重挂载后才会调用当前文章会话。
const callbackCache = new Map<string, { onChange: (text: string, changes: { from: number; to: number; inserted: string }[]) => void; onSelect: (id: string) => void }>();
const sessions = new Map<string, { state: EditorState; scroll: ReturnType<EditorView['scrollSnapshot']> }>();

class SuggestionNumber extends WidgetType {
  constructor(readonly id: string, readonly number: number, readonly kind: Suggestion['kind']) { super(); }
  toDOM() { const node = document.createElement('button'); node.type = 'button'; node.className = `inline-suggestion-number ${this.kind}`; node.textContent = String(this.number).padStart(2,'0'); node.setAttribute('data-suggestion', this.id); node.setAttribute('aria-label', '定位建议 ' + this.number); return node; }
  ignoreEvent() { return false; }
}
const marks = StateEffect.define<{ suggestions: Suggestion[]; selected: string }>();
const highlights = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(value, tr) {
    value = value.map(tr.changes);
    for (const effect of tr.effects) if (effect.is(marks)) {
      const ranges = effect.value.suggestions.filter(s => s.status === 'pending' && s.start !== null && s.end !== null && s.start < s.end && s.end <= tr.state.doc.length)
        .flatMap(s => [Decoration.widget({widget:new SuggestionNumber(s.id, effect.value.suggestions.indexOf(s)+1, s.kind),side:-1}).range(s.start!), Decoration.mark({ class: `suggestion-mark ${s.kind} ${s.id === effect.value.selected ? 'active' : ''}`, attributes: { 'data-suggestion': s.id, title: s.title } }).range(s.start!, s.end!)]);
      value = Decoration.set(ranges, true);
    }
    return value;
  }, provide: field => EditorView.decorations.from(field),
});
export type EditorHandle = { capture: () => void; undo: () => void; redo: () => void; focus: (s: Suggestion) => void };
export default function ArticleEditor({ documentKey, text, suggestions, selected, readOnly, onChange, onSelect, handle }: {
  documentKey: string; text: string; suggestions: Suggestion[]; selected: string; readOnly: boolean; onChange: (text: string, changes: { from: number; to: number; inserted: string }[]) => void;
  onSelect: (id: string) => void; handle: React.RefObject<EditorHandle | null>;
}) {
  const host = useRef<HTMLDivElement>(null), view = useRef<EditorView | null>(null);
  const leavingScroll = useRef<ReturnType<EditorView['scrollSnapshot']> | undefined>(undefined);
  const restoringScroll = useRef<ReturnType<EditorView['scrollSnapshot']> | undefined>(undefined);
  const external = useRef(externalFlags.get(documentKey) ?? { value: false }); externalFlags.set(documentKey, external.current);
  const editable = useRef(editCompartments.get(documentKey) ?? new Compartment()); editCompartments.set(documentKey, editable.current);
  const callbacks = useRef(callbackCache.get(documentKey) ?? { onChange, onSelect });
  callbacks.current.onChange = onChange; callbacks.current.onSelect = onSelect; callbackCache.set(documentKey, callbacks.current);
  useEffect(() => {
    const stored = sessions.get(documentKey);
    const cached = stored?.state.doc.toString() === text ? stored : undefined;
    const v = new EditorView({ parent: host.current!, state: cached?.state ?? EditorState.create({ doc: text, extensions: [
      // 指定 LF 为唯一分行符，原有 CR 字符保留在文本中，避免混合换行被重建。
      EditorState.lineSeparator.of('\n'), editable.current.of([EditorView.editable.of(!readOnly), EditorState.readOnly.of(readOnly)]), lineNumbers(), history(), keymap.of([...defaultKeymap, ...historyKeymap]), markdown(), EditorView.lineWrapping, highlights,
      EditorView.contentAttributes.of({ 'aria-label': '文章原文', spellcheck: 'false' }),
      EditorView.updateListener.of(update => {
        if (update.docChanged && !external.current.value) {
          const changes: { from: number; to: number; inserted: string }[] = [];
          update.changes.iterChanges((from, to, _a, _b, inserted) => changes.push({ from, to, inserted: inserted.toString() }));
          callbacks.current.onChange(update.state.doc.toString(), changes);
        }
      }),
      EditorView.domEventHandlers({ keydown(event) { if ((event.key === 'Enter' || event.key === ' ') && (event.target as HTMLElement).matches('[data-suggestion]')) { event.preventDefault(); callbacks.current.onSelect((event.target as HTMLElement).getAttribute('data-suggestion')!); return true; } return false; }, click(event) {
        const id = (event.target as HTMLElement).closest('[data-suggestion]')?.getAttribute('data-suggestion');
        if (id) callbacks.current.onSelect(id);
      } }),
    ] }), scrollTo: cached?.scroll });
    restoringScroll.current = cached?.scroll;
    view.current = v;
    handle.current = { capture: () => { leavingScroll.current = v.scrollSnapshot(); }, undo: () => { undo(v); v.focus(); }, redo: () => { redo(v); v.focus(); }, focus: s => {
      if (s.status === 'pending' && s.start !== null && s.end !== null && s.start >= 0 && s.start < s.end && s.end <= v.state.doc.length) {
        v.dispatch({ selection: { anchor: s.start, head: s.end }, effects: EditorView.scrollIntoView(s.start, { y: 'center' }) }); v.focus();
      }
    } };
    return () => { sessions.set(documentKey, { state: v.state, scroll: leavingScroll.current ?? v.scrollSnapshot() }); v.destroy(); view.current = null; handle.current = null; };
  }, []);
  useEffect(() => {
    const v = view.current;
    if (v) {
      const scroll = restoringScroll.current ?? v.scrollSnapshot(); restoringScroll.current = undefined;
      v.dispatch({ effects: [editable.current.reconfigure([EditorView.editable.of(!readOnly), EditorState.readOnly.of(readOnly)]), scroll] });
    }
    // 等待容器完成布局后测量纸张内边距，减少动画模式也能正确对齐首屏行号。
    const frame = requestAnimationFrame(() => v?.requestMeasure());
    return () => cancelAnimationFrame(frame);
  }, [readOnly]);
  useEffect(() => {
    const v = view.current; if (!v) return;
    const old = v.state.doc.toString(); if (old === text) return;
    // 外部采用/恢复也作为单笔编辑进入撤销栈，仅替换实际变化区间。
    let a = 0, b = old.length, c = text.length;
    while (a < b && a < c && old[a] === text[a]) a++;
    while (b > a && c > a && old[b-1] === text[c-1]) { b--; c--; }
    external.current.value = true;
    v.dispatch({ changes: { from: a, to: b, insert: text.slice(a,c) }, annotations: isolateHistory.of('full') });
    external.current.value = false;
  }, [text]);
  useEffect(() => { view.current?.dispatch({ effects: marks.of({ suggestions, selected }) }); }, [suggestions, selected, text]);
  return <div className="article-code" ref={host} />;
}
