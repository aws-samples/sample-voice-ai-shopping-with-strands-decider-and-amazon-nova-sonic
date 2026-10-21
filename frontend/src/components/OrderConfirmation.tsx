import type { OrderConfirmation as Order } from '../state/useShopSession';
import { formatPrice } from '../services/protocol';
import { CheckIcon } from './Icons';

export function OrderConfirmation({ order, onClose }: { order: Order; onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="order-title" onClick={(e) => e.stopPropagation()}>
        <span className="modal__icon"><CheckIcon size={28} /></span>
        <h2 id="order-title">Order placed</h2>
        <p className="muted">Thanks! This is a demo store, so no payment was taken and nothing will ship.</p>
        <dl className="specs">
          <div className="specs__row"><dt>Order</dt><dd>{order.orderId}</dd></div>
          <div className="specs__row"><dt>Items</dt><dd>{order.itemCount}</dd></div>
          <div className="specs__row"><dt>Total</dt><dd>{formatPrice(order.totalCents)}</dd></div>
          <div className="specs__row"><dt>Placed</dt><dd>{new Date(order.placedAt).toLocaleString()}</dd></div>
        </dl>
        <button className="button button--primary button--block" onClick={onClose} autoFocus>Continue shopping</button>
      </div>
    </div>
  );
}
