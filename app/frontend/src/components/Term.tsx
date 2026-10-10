import { useId, useState } from 'react';
import { glossary } from '../glossary';
export function Term({ name, label }: { name: keyof typeof glossary; label?: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return <span className="term"><button type="button" aria-describedby={id}
    aria-expanded={open} onClick={() => setOpen(!open)} onBlur={() => setOpen(false)}>{label || name}</button>
    <span id={id} role="tooltip" className={open ? 'definition open' : 'definition'}>{glossary[name]}</span></span>;
}
