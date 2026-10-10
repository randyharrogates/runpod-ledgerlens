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
  function inline(line: string) {
    return line.replace(/(\[p\d+:r\d+\])[^\S\n]+([.,;:!?])/g, '$1$2')
      .split(/(\[p\d+:r\d+\])/g).map((part, i) => {
        const match = part.match(/^\[(p\d+):r(\d+)\]$/);
        return match ? <button key={i} onClick={() => jump({
          page_id: match[1], row_index: Number(match[2]) - 1,
        })}>{part}</button> : <React.Fragment key={i}>{part}</React.Fragment>;
      });
  }
  const blocks: React.ReactNode[] = [];
  let items: React.ReactNode[] = [];
  function flush() {
    if (items.length) {
      blocks.push(<ul key={`list-${blocks.length}`}>{items}</ul>);
      items = [];
    }
  }
  text.split('\n').forEach((line, i) => {
    const trimmed = line.trim();
    const heading = trimmed.replace(/^#{1,6}\s+/, '').replace(/^\*\*(.*?)\*\*$/, '$1');
    const bullet = trimmed.match(/^[•*-]\s+(.*)$/);
    if (bullet) {
      items.push(<li key={i}>{inline(bullet[1])}</li>);
      return;
    }
    flush();
    if (headings.has(heading.toLowerCase())) blocks.push(<h3 key={i}>{heading}</h3>);
    else if (trimmed) blocks.push(<p key={i}>{inline(line)}</p>);
  });
  flush();
  return <>{blocks}</>;
}
