import type { CartLine } from '../services/protocol';
import { formatPrice } from '../services/protocol';
import { CartIcon, CloseIcon } from './Icons';

export function CartPanel({ items, itemCount, totalCents, onCheckout, onRemove, disabled }: {
  items: CartLine[];
  itemCount: number;
  totalCents: number;
  onCheckout: () => void;
  /** Remove one line (or say "remove the ... from my cart"). */
  onRemove: (asin: string) => void;
  disabled: boolean;
}) {
  return (
    <section className="panel cart" aria-label="Cart">
      <header className="panel__header">
        <span className="panel__title"><CartIcon size={16} /> Cart</span>
        <span className="muted">{itemCount} item{itemCount === 1 ? '' : 's'}</span>
      </header>
      {items.length === 0 ? (
        <p className="muted cart__empty">Your cart is empty. Say “add it to my cart” when you find something you like.</p>
      ) : (
        <ul className="cart__lines">
          {items.map((line) => (
            <li key={line.asin} className="cart__line">
              <span className="cart__title">{line.title}</span>
              <span className="cart__qty">× {line.quantity}</span>
              <span className="cart__price">{formatPrice(line.priceCents * line.quantity)}</span>
              <button className="cart__remove icon-button" onClick={() => onRemove(line.asin)} disabled={disabled}
                aria-label={`Remove ${line.title} from the cart`} title="Remove from cart"><CloseIcon size={14} /></button>
            </li>
          ))}
        </ul>
      )}
      <footer className="cart__footer">
        <div className="cart__total"><span>Total</span><strong>{formatPrice(totalCents)}</strong></div>
        <button className="button button--primary button--block" onClick={onCheckout} disabled={disabled || items.length === 0}>
          Place order
        </button>
        <p className="hint">Demo store: no payment is taken.</p>
      </footer>
    </section>
  );
}
