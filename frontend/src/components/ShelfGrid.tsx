import { useEffect, useRef, useState } from 'react';
import type { ShelfItem } from '../services/protocol';
import { formatPrice } from '../services/protocol';
import { Stars } from './Icons';

const MIN_CARD_PX = 190;
const GAP_PX = 20;

interface ShelfGridProps {
  items: ShelfItem[];
  tentative: boolean;
  loading: boolean;
  /** A shelf has arrived from the host: an empty one now means no product matched. */
  received: boolean;
  onOpen: (asin: string) => void;
  onColumns: (columns: number) => void;
}

/**
 * Product grid with 2-6 columns. The column count is reported to the host
 * (ui.layout) so "the one on the left" resolves to what the shopper sees.
 * Cards carry their position number for "the third one".
 */
export function ShelfGrid({ items, tentative, loading, onOpen, onColumns, received }: ShelfGridProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(4);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const fit = Math.floor((entry.contentRect.width + GAP_PX) / (MIN_CARD_PX + GAP_PX));
      setColumns(Math.min(6, Math.max(2, fit)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => onColumns(columns), 150);
    return () => clearTimeout(timer);
  }, [columns, onColumns]);

  return (
    <div ref={ref} className={`shelf${tentative ? ' shelf--tentative' : ''}`} aria-busy={loading}>
      <div className="shelf__grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
        {items.length === 0 && received && !loading
          ? <div className="shelf__empty">No product matches these choices. Remove a chip, or say what to change.</div>
          : items.length === 0
          ? Array.from({ length: columns * 2 }, (_, i) => <div key={i} className="card card--skeleton" />)
          : items.map((item, index) => (
            <button key={item.asin} className="card" onClick={() => onOpen(item.asin)}
              aria-label={`${index + 1}. ${item.title}, ${formatPrice(item.priceCents)}`}>
              <span className="card__position" aria-hidden>{index + 1}</span>
              <span className="card__media">
                <img src={item.imageUrl} alt="" loading="lazy" />
              </span>
              <span className="card__body">
                <span className="card__brand">{item.brand}</span>
                <span className="card__title">{item.title}</span>
                <span className="card__footer">
                  <span className="card__price">{formatPrice(item.priceCents)}</span>
                  {item.rating > 0 && (
                    <span className="card__rating"><Stars rating={item.rating} size={13} /> {item.rating.toFixed(1)}</span>
                  )}
                </span>
              </span>
            </button>
          ))}
      </div>
    </div>
  );
}
