import type { Chip, Slot } from '../services/protocol';
import { CloseIcon, SparkIcon } from './Icons';

interface ChipBarProps {
  chips: Chip[];
  remembered: string[];
  counts: { catalog: number; afterFilters: number; candidates: number } | null;
  shown: number;
  tentative: boolean;
  onRemove: (slot: Slot) => void;
}

// Remembered hints stay small next to the chips that drive the search.
const MAX_HINTS = 3;
const HINT_CHARS = 40;

export function ChipBar({ chips, remembered, counts, shown, tentative, onRemove }: ChipBarProps) {
  return (
    <section className="chip-bar" aria-label="What I understood">
      <div className="chip-bar__chips">
        {chips.length === 0 ? (
          <span className="chip-bar__empty">Your preferences appear here as you talk: who it is for, style, category and budget.</span>
        ) : (
          chips.map((chip) => (
            <span key={chip.slot} className={`chip${chip.confidence < 0.8 ? ' chip--tentative' : ''}`}>
              <span className="chip__label">{chip.label}</span>
              <span className="chip__value">{chip.value}</span>
              {chip.slot !== 'budget' && <span className="chip__confidence">{Math.round(chip.confidence * 100)}%</span>}
              <button className="chip__remove" onClick={() => onRemove(chip.slot)} aria-label={`Remove ${chip.label} ${chip.value}`}>
                <CloseIcon size={12} />
              </button>
            </span>
          ))
        )}
        {remembered.slice(0, MAX_HINTS).map((hint, index) => (
          <span key={`${index}-${hint}`} className="chip chip--remembered"
            title={`Remembered from an earlier visit: ${hint}. Used as a hint, never as a filter.`}>
            <SparkIcon size={12} />
            <span className="chip__value">{hint.length > HINT_CHARS ? `${hint.slice(0, HINT_CHARS - 1)}…` : hint}</span>
          </span>
        ))}
      </div>
      {counts && (
        <div className="chip-bar__meta">
          {tentative && <span className="badge badge--tentative" title="Some preferences are still uncertain">Tentative</span>}
          {chips.length === 0 ? (
            <span>Featured picks <span className="muted">· {counts.catalog.toLocaleString()} products</span></span>
          ) : (
            <span>
              Showing <strong>{shown}</strong> of {counts.afterFilters.toLocaleString()} matches
              <span className="muted"> · {counts.catalog.toLocaleString()} products</span>
            </span>
          )}
        </div>
      )}
    </section>
  );
}
