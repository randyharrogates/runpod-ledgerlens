import React, { useEffect, useRef, useState } from 'react';
import { modelState } from '../models';
import { api, Citation } from '../api';

export function Memo({ session, model, jump, status = {} }: {
  status?: Record<string, unknown>;
  session: string;
  model: string;
  jump: (citation: Citation) => void;
}) {
  const [text, setText] = useState('');
  const [unverified, setUnverified] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [cached, setCached] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  const [error, setError] = useState('');
  const stream = useRef<EventSource | null>(null);
  useEffect(() => () => stream.current?.close(), []);

  function generate() {
    stream.current?.close();
    setText('');
    setUnverified([]);
    setCached(false);
    setElapsed(0);
    started.current = Date.now();
    setError('');
    setBusy(true);
    const source = api.memo(session, model);
    stream.current = source;
    source.addEventListener('chunk', event => {
      setText(value => value + JSON.parse((event as MessageEvent).data).delta);
    });
    source.addEventListener('final', event => {
      const final = JSON.parse((event as MessageEvent).data);
      setUnverified(Array.isArray(final.unverified) ? final.unverified.filter((value: unknown) => typeof value === 'string') : []);
      setCached(final.source === 'cached-demo' || final.metrics?.source === 'cached-demo');
      source.close();
      setBusy(false);
    });
    source.addEventListener('error', event => {
      setError(event instanceof MessageEvent
        ? JSON.parse(event.data).message
        : 'Connection lost. Retry to generate again.');
      source.close();
      setBusy(false);
    });
  }

  return (
    <>
      {cached && <p className="cached">Cached demo · no live model call</p>}
      {busy && <p role="status">Writing · {elapsed}s elapsed{['asleep', 'waking up'].includes(modelState(status, model)) && <span> · Waiting for a GPU: the model was asleep. This can take a few minutes.</span>}</p>}
      <article className="memo">
        {!text && <p>Generate a credit memo from the checked rows, cash flow and cited red flags.</p>}
      <button disabled={busy || !session} onClick={generate}>
        {busy ? 'Writing memo…' : 'Generate memo'}
      </button>
      <p>Numbers are computed from extracted rows; the model writes the prose. Any number it adds is highlighted.</p>
        <MemoContent text={text} jump={jump} unverified={unverified} />
      </article>
      {unverified.length > 0 && <p role="alert">{unverified.length} number(s) in this memo are not in the computed analysis and are highlighted. Check them before use.</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}

const headings = new Set(['summary', 'cash flow', 'concentration', 'red flags', 'questions for the applicant']);

export function MemoContent({ text, jump, unverified = [] }: { text: string; jump: (citation: Citation) => void; unverified?: string[] }) {
  function highlight(part: string): React.ReactNode[] {
    return part.split(/(\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|(?<![\d:r])\d{4,}(?![\d,]|\.\d))/g).map((token, i) =>
      unverified.includes(token)
        ? <mark key={i} className="unverified" title="Not in the computed analysis — check before use">{token}</mark>
        : token);
  }
  // The model writes citations as [p1:r4], (p1:r4), [p1:r2; p2:r1], (p1:r1–p1:r18) or bare p1:r4.
  const ref = String.raw`p\d+:r\d+(?:\s*[–-]\s*(?:p\d+:)?r\d+)?`;
  const group = new RegExp(String.raw`([\[(]${ref}(?:\s*[;,]\s*${ref})*[\])]|${ref})`, 'g');
  const whole = new RegExp(String.raw`^(?:[\[(]${ref}(?:\s*[;,]\s*${ref})*[\])]|${ref})$`);
  function inline(line: string): React.ReactNode[] {
    return line.replace(new RegExp(String.raw`([\])])[^\S\n]+([.,;:!?])`, 'g'), '$1$2')
      .split(new RegExp(String.raw`(\*\*[^*]+\*\*|${group.source.slice(1, -1)})`, 'g')).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i}>{inline(part.slice(2, -2))}</strong>;
        }
        if (whole.test(part)) {
          return <React.Fragment key={i}>{part.replace(/^[[(]|[\])]$/g, '').split(/\s*[;,]\s*/).map((item, j) => {
            const match = item.match(/^(p\d+):r(\d+)/);
            return match ? <button key={j} onClick={() => jump({
              page_id: match[1], row_index: Number(match[2]) - 1,
            })}>[{item}]</button> : item;
          })}</React.Fragment>;
        }
        return <React.Fragment key={i}>{highlight(part)}</React.Fragment>;
      });
  }
  const blocks: React.ReactNode[] = [];
  let items: React.ReactNode[] = [];
  let listType: 'ul' | 'ol' = 'ul';
  function flush() {
    if (items.length) {
      const List = listType;
      blocks.push(<List key={`list-${blocks.length}`}>{items}</List>);
      items = [];
    }
  }
  text.split('\n').forEach((line, i) => {
    const trimmed = line.trim();
    const heading = trimmed.replace(/^#{1,6}\s+/, '').replace(/^\*\*(.*?)\*\*$/, '$1');
    const bullet = trimmed.match(/^[•*-]\s+(.*)$/);
    const numbered = trimmed.match(/^\d+\.\s+(.*)$/);
    if (bullet || numbered) {
      const nextType = numbered ? 'ol' : 'ul';
      if (nextType !== listType) flush();
      listType = nextType;
      items.push(<li key={i}>{inline((numbered || bullet)![1])}</li>);
      return;
    }
    flush();
    if (/^(?:---|\*\*\*|___)$/.test(trimmed)) return;
    // Known sections, and a whole line in bold (e.g. "**Credit Memo**"); "## Summ" mid-stream stays text.
    if (headings.has(heading.toLowerCase()) || /^\*\*[^*]+\*\*$/.test(trimmed)) blocks.push(<h3 key={i}>{inline(heading)}</h3>);
    else if (trimmed) blocks.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return <>{blocks}</>;
}
