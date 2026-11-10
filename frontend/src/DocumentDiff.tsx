import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Button, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Tooltip, useMediaQuery } from '@mui/material';
import ArrowUpward from '@mui/icons-material/ArrowUpward';
import ArrowDownward from '@mui/icons-material/ArrowDownward';
import OpenInFull from '@mui/icons-material/OpenInFull';
import { compareLine, compareTexts, lineContent, type DiffSection, type TextPart } from './textDiff';
import './diff.css';

type Props = { before: string; after: string; beforeLabel?: string; afterLabel?: string; expandable?: boolean; initialLayout?: 'unified' | 'split' };
const PAGE_SIZE = 100;

function visibleText(text: string) {
  return text.split('\uFEFF').map((part, index) => <span key={index}>{index > 0 && <span className="diff-invisible" title="字节序标记">BOM</span>}{part}</span>);
}

function Cell({ line, number, kind, parts, label, endingChanged }: {
  line?: string; number?: number; kind: 'equal' | 'delete' | 'insert'; parts?: TextPart[]; label: string; endingChanged?: boolean;
}) {
  if (line === undefined) return <div className="diff-cell diff-placeholder" aria-hidden="true" />;
  const { text, ending } = lineContent(line);
  const action = kind === 'delete' ? '删除' : kind === 'insert' ? '新增' : '未修改';
  return <div className={`diff-cell ${kind}`} role="group" aria-label={`${label}第 ${number} 行，${action}`}>
    <span className="diff-line-number" aria-hidden="true">{number}</span><span className="diff-line-sign" aria-hidden="true">{kind === 'delete' ? '−' : kind === 'insert' ? '+' : ' '}</span>
    <code>{!text ? <span className="diff-invisible">空行</span> : parts ? parts.map((part, index) => part.changed
      ? <mark key={index} className={`diff-inline-${kind}`}>{visibleText(part.text)}</mark> : <span key={index}>{visibleText(part.text)}</span>) : visibleText(text)}
      {endingChanged && <span className="diff-ending">{ending === '\r\n' ? 'CRLF' : ending === '\r' ? 'CR' : ending === '\n' ? 'LF' : '无行尾换行'}</span>}
    </code>
  </div>;
}

function ChangedRow({ section, index, beforeLabel, afterLabel }: { section: DiffSection; index: number; beforeLabel: string; afterLabel: string }) {
  const before = section.before[index], after = section.after[index];
  const parts = useMemo(() => compareLine(before, after), [before, after]);
  const endingChanged = before !== undefined && after !== undefined && lineContent(before).ending !== lineContent(after).ending;
  return <div className="diff-row diff-changed-row">
    <Cell line={before} number={section.beforeStart + index} kind="delete" parts={parts.before} label={beforeLabel} endingChanged={endingChanged} />
    <Cell line={after} number={section.afterStart + index} kind="insert" parts={parts.after} label={afterLabel} endingChanged={endingChanged} />
  </div>;
}

function Section({ section, index, last, changeIndex, selected, fullContext, beforeLabel, afterLabel }: {
  section: DiffSection; index: number; last: boolean; changeIndex: number; selected: boolean; fullContext: boolean; beforeLabel: string; afterLabel: string;
}) {
  const [expanded, setExpanded] = useState(false), [shown, setShown] = useState(PAGE_SIZE);
  const region = useRef<HTMLElement>(null), toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => { setExpanded(false); setShown(PAGE_SIZE); }, [section]);
  const count = Math.max(section.before.length, section.after.length);
  function contextRows(from: number, to: number) {
    return Array.from({ length: to - from }, (_, offset) => {
      const row = from + offset;
      return <div className="diff-row diff-context-row" key={row}>
        <Cell line={section.before[row]} number={section.beforeStart + row} kind="equal" label={beforeLabel} />
        <Cell line={section.after[row]} number={section.afterStart + row} kind="equal" label={afterLabel} />
      </div>;
    });
  }
  if (section.kind === 'equal' && !fullContext && !expanded) {
    const head = index === 0 ? 0 : Math.min(3, count), tail = last ? count : Math.max(head, count - 3);
    if (tail <= head) return <>{contextRows(0, count)}</>;
    return <>{contextRows(0, head)}<button ref={toggle} className="diff-context-toggle" aria-expanded={false} onClick={() => { setExpanded(true); requestAnimationFrame(() => region.current?.focus({ preventScroll: true })); }}>展开 {tail - head} 行未改内容</button>{contextRows(tail, count)}</>;
  }
  const end = Math.min(count, shown);
  return <section ref={region} tabIndex={-1} className={`diff-section ${selected ? 'is-current' : ''}`} data-change-index={section.kind === 'change' ? changeIndex : undefined} aria-label={section.kind === 'change' ? `第 ${changeIndex + 1} 处改动` : '未修改的上下文'}>
    {section.kind === 'change' && <div className="diff-section-title">改动 {changeIndex + 1}<span>−{section.before.length} / +{section.after.length} 行</span></div>}
    {section.kind === 'equal' ? contextRows(0, end) : Array.from({ length: end }, (_, row) => <ChangedRow key={row} section={section} index={row} beforeLabel={beforeLabel} afterLabel={afterLabel} />)}
    {count > end && <button className="diff-context-toggle" onClick={() => setShown(value => value + PAGE_SIZE)}>继续显示 {Math.min(PAGE_SIZE, count - end)} 行（剩余 {count - end} 行）</button>}
    {section.kind === 'equal' && expanded && !fullContext && <button className="diff-context-toggle" aria-expanded={true} onClick={() => { setExpanded(false); setShown(PAGE_SIZE); requestAnimationFrame(() => toggle.current?.focus()); }}>折叠未改内容</button>}
  </section>;
}

export default function DocumentDiff({ before, after, beforeLabel = '修改前', afterLabel = '修改后', expandable = true, initialLayout = 'unified' }: Props) {
  const model = useMemo(() => compareTexts(before, after), [before, after]);
  const [layout, setLayout] = useState(initialLayout), [fullContext, setFullContext] = useState(false);
  const [selected, setSelected] = useState(0), [enlarged, setEnlarged] = useState(false);
  const [wide, setWide] = useState(false);
  const container = useRef<HTMLElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)');
  const dialogTitle = useId();
  useEffect(() => { setSelected(0); setFullContext(false); }, [before, after]);
  useEffect(() => {
    const observer = new ResizeObserver(entries => setWide(entries[0].contentRect.width >= 620));
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  function navigate(index: number) {
    setSelected(index);
    viewport.current?.querySelector(`[data-change-index="${index}"]`)?.scrollIntoView({ block: 'nearest', behavior: reducedMotion ? 'instant' : 'smooth' });
  }
  let changeIndex = -1;
  return <section ref={container} className="document-diff" aria-label="可视化差异对比">
    <div className="diff-summary"><strong>{model.changes ? `${model.changes} 处改动` : '正文相同'}</strong><span className="diff-deleted">−{model.removed} 行删除</span><span className="diff-added">+{model.added} 行新增</span>
      {expandable && <Tooltip title="放大对比"><IconButton size="small" aria-label="放大对比" onClick={() => setEnlarged(true)}><OpenInFull fontSize="small" /></IconButton></Tooltip>}
    </div>
    <div className="diff-toolbar"><div className="diff-layout" role="group" aria-label="差异布局"><Button size="small" aria-pressed={!wide || layout === 'unified'} onClick={() => setLayout('unified')}>逐行</Button><Tooltip title={wide ? '' : '加宽窗口或放大对比后可并排查看'}><span><Button size="small" disabled={!wide} aria-pressed={wide && layout === 'split'} onClick={() => setLayout('split')}>并排</Button></span></Tooltip></div>
      <Button size="small" aria-pressed={fullContext} onClick={() => setFullContext(value => !value)}>{fullContext ? '仅看改动' : '完整上下文'}</Button>
      {!!model.changes && <div className="diff-navigation"><span aria-live="polite">{selected + 1} / {model.changes}</span><IconButton size="small" aria-label="上一处改动" disabled={selected === 0} onClick={() => navigate(selected - 1)}><ArrowUpward fontSize="small" /></IconButton><IconButton size="small" aria-label="下一处改动" disabled={selected >= model.changes - 1} onClick={() => navigate(selected + 1)}><ArrowDownward fontSize="small" /></IconButton></div>}
    </div>
    {model.coarse && <p className="diff-note">大段改写按整行标记，可继续展开查看全部内容。</p>}
    {!model.changes && !fullContext ? <p className="diff-empty" role="status">两份正文完全一致，没有需要确认的文本改动。</p> : <div ref={viewport} className={`diff-viewport ${wide ? layout : 'unified'}`} tabIndex={0} role="region" aria-label="差异正文">
      <div className="diff-column-headings"><span>− {beforeLabel}</span><span>+ {afterLabel}</span></div>
      {model.sections.map((section, index) => {
        if (section.kind === 'change') changeIndex++;
        return <Section key={section.id} section={section} index={index} last={index === model.sections.length - 1} changeIndex={changeIndex} selected={section.kind === 'change' && changeIndex === selected} fullContext={fullContext} beforeLabel={beforeLabel} afterLabel={afterLabel} />;
      })}
    </div>}
    {expandable && <Dialog open={enlarged} onClose={() => setEnlarged(false)} fullWidth maxWidth="lg" aria-labelledby={dialogTitle}><DialogTitle id={dialogTitle}>对比{beforeLabel}与{afterLabel}</DialogTitle><DialogContent><DocumentDiff before={before} after={after} beforeLabel={beforeLabel} afterLabel={afterLabel} expandable={false} initialLayout="split" /></DialogContent><DialogActions><Button onClick={() => setEnlarged(false)}>返回审阅</Button></DialogActions></Dialog>}
  </section>;
}
