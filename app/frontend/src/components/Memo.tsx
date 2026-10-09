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
        {text.replace(/(\[p\d+:r\d+\])\s+([.,;:])/g, '$1$2')
          .split(/(\[p\d+:r\d+\])/g).map((part, i) => {
            const match = part.match(/^\[(p\d+):r(\d+)\]$/);
            return match ? (
              <button key={i} onClick={() => jump({
                page_id: match[1], row_index: Number(match[2]) - 1,
              })}>
                {part}
              </button>
            ) : <React.Fragment key={i}>{part}</React.Fragment>;
          })}
      </article>
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}
