import React, { useEffect, useRef, useState } from 'react';
import { api, Citation } from '../api';

export function Memo({ session, model, jump }: {
  session: string;
  model: string;
  jump: (citation: Citation) => void;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const stream = useRef<EventSource | null>(null);
  useEffect(() => () => stream.current?.close(), []);

  function generate() {
    stream.current?.close();
    setText('');
    setError('');
    setBusy(true);
    const source = api.memo(session, model);
    stream.current = source;
    source.addEventListener('chunk', event => {
      setText(value => value + JSON.parse((event as MessageEvent).data).delta);
    });
    source.addEventListener('final', () => {
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
      <button disabled={busy || !session} onClick={generate}>
        {busy ? 'Writing memo…' : 'Generate memo'}
      </button>
      <p>Numbers are computed from extracted rows; the model only writes the prose.</p>
      <article className="memo">
        <MemoContent text={text} jump={jump} />
      </article>
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}

const headings = new Set(['summary', 'cash flow', 'concentration', 'red flags', 'questions for the applicant']);

export function MemoContent({ text, jump }: { text: string; jump: (citation: Citation) => void }) {
  function inline(line: string): React.ReactNode[] {
    return line.replace(/(\[p\d+:r\d+(?:[–-]r\d+)?(?:\s*[;,]\s*p\d+:r\d+(?:[–-]r\d+)?)*\])[^\S\n]+([.,;:!?])/g, '$1$2')
      .split(/(\*\*[^*]+\*\*|\[p\d+:r\d+(?:[–-]r\d+)?(?:\s*[;,]\s*p\d+:r\d+(?:[–-]r\d+)?)*\])/g).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i}>{inline(part.slice(2, -2))}</strong>;
        }
        if (/^\[p\d+:r\d+(?:[–-]r\d+)?(?:\s*[;,]\s*p\d+:r\d+(?:[–-]r\d+)?)*\]$/.test(part)) {
          return <React.Fragment key={i}>{part.slice(1, -1).split(/\s*[;,]\s*/).map((ref, j) => {
            const match = ref.match(/^(p\d+):r(\d+)(?:[–-]r\d+)?$/);
            return match ? <button key={j} onClick={() => jump({
              page_id: match[1], row_index: Number(match[2]) - 1,
            })}>[{ref}]</button> : ref;
          })}</React.Fragment>;
        }
        return <React.Fragment key={i}>{part}</React.Fragment>;
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
    if (headings.has(heading.toLowerCase()) || /^\*\*[^*]+\*\*$/.test(trimmed)) blocks.push(<h3 key={i}>{heading}</h3>);
    else if (trimmed) blocks.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return <>{blocks}</>;
}
