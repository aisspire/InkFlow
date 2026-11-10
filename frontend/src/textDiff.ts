export type Edit = { kind: 'equal' | 'delete' | 'insert'; values: string[] };
type Budget = { remaining: number };
export type DiffSection = { id: number; kind: 'equal' | 'change'; before: string[]; after: string[]; beforeStart: number; afterStart: number };
export type TextDiff = { sections: DiffSection[]; added: number; removed: number; changes: number; coarse: boolean };
export type TextPart = { text: string; changed: boolean };

// Keep line endings in the compared tokens so CRLF, blank lines and a missing final newline remain visible changes.
export function textLines(text: string): string[] {
  return text.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g)?.filter(Boolean) ?? [];
}
export function lineContent(line: string) {
  const ending = line.endsWith('\r\n') ? '\r\n' : /[\r\n]$/.test(line) ? line.slice(-1) : '';
  return { text: line.slice(0, line.length - ending.length), ending };
}

function append(edits: Edit[], kind: Edit['kind'], values: string[]) {
  if (!values.length) return;
  const previous = edits.at(-1);
  if (previous?.kind === kind) previous.values = previous.values.concat(values);
  else edits.push({ kind, values });
}

// Bounded Myers matching. Large rewrites fall back to exact delete/insert blocks, never shortened text.
function match(before: string[], after: string[], budget: Budget): { edits: Edit[]; coarse: boolean } {
  let start = 0, endBefore = before.length, endAfter = after.length;
  while (start < endBefore && start < endAfter && before[start] === after[start]) start++;
  while (endBefore > start && endAfter > start && before[endBefore - 1] === after[endAfter - 1]) { endBefore--; endAfter--; }
  const a = before.slice(start, endBefore), b = after.slice(start, endAfter);
  const edits: Edit[] = [];
  append(edits, 'equal', before.slice(0, start));
  let middle: Edit[] | null = null;
  if (!a.length || !b.length) middle = [{ kind: 'delete', values: a }, { kind: 'insert', values: b }];
  else {
    const distance = Math.min(a.length + b.length, 512), offset = distance + 1;
    const frontier = new Int32Array(2 * distance + 3).fill(-1);
    frontier[offset + 1] = 0;
    const trace: Int32Array[] = [];
    search: for (let d = 0; d <= distance && budget.remaining > 0; d++) {
      trace.push(frontier.slice());
      for (let k = -d; k <= d; k += 2) {
        if (--budget.remaining < 0) break search;
        let x = k === -d || k !== d && frontier[offset + k - 1] < frontier[offset + k + 1]
          ? frontier[offset + k + 1] : frontier[offset + k - 1] + 1;
        let y = x - k;
        while (x < a.length && y < b.length && a[x] === b[y]) {
          if (--budget.remaining < 0) break search;
          x++; y++;
        }
        frontier[offset + k] = x;
        if (x < a.length || y < b.length) continue;
        const reversed: Edit[] = [];
        const push = (kind: Edit['kind'], value: string) => {
          if (reversed.at(-1)?.kind === kind) reversed.at(-1)!.values.push(value);
          else reversed.push({ kind, values: [value] });
        };
        for (let step = d; step >= 0; step--) {
          const previous = trace[step], diagonal = x - y;
          const previousK = diagonal === -step || diagonal !== step && previous[offset + diagonal - 1] < previous[offset + diagonal + 1]
            ? diagonal + 1 : diagonal - 1;
          const previousX = previous[offset + previousK], previousY = previousX - previousK;
          while (x > previousX && y > previousY) { push('equal', a[--x]); y--; }
          if (step === 0) break;
          if (x === previousX) push('insert', b[--y]); else push('delete', a[--x]);
        }
        middle = reversed.reverse().map(edit => ({ ...edit, values: edit.values.reverse() }));
        break search;
      }
    }
  }
  const coarse = middle === null;
  for (const edit of middle ?? [{ kind: 'delete' as const, values: a }, { kind: 'insert' as const, values: b }]) append(edits, edit.kind, edit.values);
  append(edits, 'equal', before.slice(endBefore));
  return { edits, coarse };
}

export function compareTexts(before: string, after: string): TextDiff {
  const { edits, coarse } = match(textLines(before), textLines(after), { remaining: 600_000 });
  const sections: DiffSection[] = [];
  let beforeStart = 1, afterStart = 1, added = 0, removed = 0;
  for (const edit of edits) {
    if (edit.kind === 'equal') {
      sections.push({ id: sections.length, kind: 'equal', before: edit.values, after: edit.values, beforeStart, afterStart });
      beforeStart += edit.values.length; afterStart += edit.values.length;
    } else {
      let section = sections.at(-1);
      if (section?.kind !== 'change') {
        section = { id: sections.length, kind: 'change', before: [], after: [], beforeStart, afterStart };
        sections.push(section);
      }
      if (edit.kind === 'delete') { section.before = section.before.concat(edit.values); beforeStart += edit.values.length; removed += edit.values.length; }
      else { section.after = section.after.concat(edit.values); afterStart += edit.values.length; added += edit.values.length; }
    }
  }
  return { sections, added, removed, changes: sections.filter(section => section.kind === 'change').length, coarse };
}

export function compareLine(before: string | undefined, after: string | undefined): { before: TextPart[]; after: TextPart[] } {
  const a = lineContent(before ?? '').text, b = lineContent(after ?? '').text;
  // Avoid allocating a character matrix or a huge character array for a single very long paragraph.
  if (before === undefined || after === undefined || a.length + b.length > 16_000) {
    return { before: [{ text: a, changed: a !== b }], after: [{ text: b, changed: a !== b }] };
  }
  const { edits } = match(Array.from(a), Array.from(b), { remaining: 12_000 });
  return {
    before: edits.filter(edit => edit.kind !== 'insert').map(edit => ({ text: edit.values.join(''), changed: edit.kind === 'delete' })),
    after: edits.filter(edit => edit.kind !== 'delete').map(edit => ({ text: edit.values.join(''), changed: edit.kind === 'insert' })),
  };
}
