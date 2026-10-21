import { useEffect } from 'react';
import type { ProductDetail as Detail } from '../services/protocol';
import { formatPrice } from '../services/protocol';
import { CartIcon, CloseIcon, SparkIcon, Stars } from './Icons';

interface ProductDetailProps {
  product: Detail;
  why: string;
  onClose: () => void;
  onAddToCart: () => void;
}

export function ProductDetail({ product, why, onClose, onAddToCart }: ProductDetailProps) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const details: [string, string | null | undefined][] = [
    ['Brand', product.brand],
    ['Category', product.category],
    ['Color', product.color],
    ['Material', product.material],
    ['Size', product.size],
  ];

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <aside className="sheet" role="dialog" aria-modal="true" aria-label={product.title} onClick={(e) => e.stopPropagation()}>
        <button className="sheet__close icon-button" onClick={onClose} aria-label="Close"><CloseIcon /></button>
        <div className="sheet__media">
          {product.images.largeUrl && <img src={product.images.largeUrl} alt={product.title} />}
        </div>
        <div className="sheet__content">
          <span className="eyebrow">{product.brand}</span>
          <h2 className="sheet__title">{product.title}</h2>
          <div className="sheet__price-row">
            <span className="sheet__price">{formatPrice(product.priceCents)}</span>
            {product.averageRating !== undefined && product.averageRating > 0 && (
              <span className="sheet__rating">
                <Stars rating={product.averageRating} /> {product.averageRating.toFixed(1)}
                {product.ratingNumber ? <span className="muted"> ({product.ratingNumber.toLocaleString()} ratings)</span> : null}
              </span>
            )}
          </div>

          <div className="why">
            <span className="why__title"><SparkIcon size={14} /> Why this pick</span>
            <p>{why}</p>
          </div>

          <button className="button button--primary button--block" onClick={onAddToCart} disabled={product.available === false}>
            <CartIcon size={18} /> {product.available === false ? 'Currently unavailable' : 'Add to cart'}
          </button>
          <p className="hint">Or just say “add it to my cart”.</p>

          <dl className="specs">
            {details.filter(([, v]) => v).map(([k, v]) => (
              <div key={k} className="specs__row"><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>

          {product.features.length > 0 && (
            <section className="sheet__section">
              <h3>Highlights</h3>
              <ul className="features">{product.features.slice(0, 6).map((f) => <li key={f}>{f}</li>)}</ul>
            </section>
          )}
          {product.description && (
            <section className="sheet__section">
              <h3>Description</h3>
              <p className="description">{product.description}</p>
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}
