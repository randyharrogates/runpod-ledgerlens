import { useId, useState } from 'react';
import { glossary } from '../glossary';
export function Term({ name }: { name: keyof typeof glossary }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return <span className="term"><button type="button" aria-describedby={id}
    aria-expanded={open} onClick={() => setOpen(!open)} onBlur={() => setOpen(false)}>{name}</button>
    <span id={id} role="tooltip" className={open ? 'definition open' : 'definition'}>{glossary[name]}</span></span>;
}
