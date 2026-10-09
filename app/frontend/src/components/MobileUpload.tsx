import { useState } from 'react';
import { api } from '../api';

export function Upload({ session, done, mobile = false }: {
  session: string;
  done: () => void;
  mobile?: boolean;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const [message, setMessage] = useState('');
  async function send(files: File[]) {
    if (!files.length) return;
    setMessage('');
    setProgress(0);
    try {
      await api.upload(session, files, setProgress);
      setMessage(mobile ? 'Sent ✓ — check the laptop' : 'Pages uploaded');
      done();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setProgress(null);
    }
  }
  return (
    <section className="drop" onDragOver={e => e.preventDefault()} onDrop={e => {
      e.preventDefault();
      if (progress === null) void send(Array.from(e.dataTransfer.files));
    }}>
      <h2>{mobile ? 'Send pages to your analyst' : 'Start with the evidence'}</h2>
      <p>PNG, JPEG or WEBP · up to 15 MB per page · 30 pages</p>
      <label className="button">
        {mobile ? 'Take photo' : 'Choose files or drop them here'}
        <input disabled={!session || progress !== null} type="file" accept="image/*"
          capture={mobile ? 'environment' : undefined} multiple onChange={e => {
            void send(Array.from(e.target.files || []));
            e.target.value = '';
          }} />
      </label>
      {progress !== null && <progress value={progress} max={100} />}
      <p role="status">{message}</p>
    </section>
  );
}
