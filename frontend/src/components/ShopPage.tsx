import { useCallback, useEffect, useRef, useState } from 'react';
import type { SessionTransport } from '../services/protocol';
import { useShopSession } from '../state/useShopSession';
import { AssistantPanel } from './AssistantPanel';
import { CartPanel } from './CartPanel';
import { ChipBar } from './ChipBar';
import { DebugOverlay } from './DebugOverlay';
import { PopoutWindow } from './PopoutWindow';
import { Footer } from './Footer';
import { Header } from './Header';
import { CloseIcon, InfoIcon } from './Icons';
import { OrderConfirmation } from './OrderConfirmation';
import { ProductDetail } from './ProductDetail';
import { ShelfGrid } from './ShelfGrid';

interface ShopPageProps {
  transport: SessionTransport;
  userName?: string;
  onSignOut?: () => void;
}

export function ShopPage({ transport, userName, onSignOut }: ShopPageProps) {
  const session = useShopSession(transport);
  const { state } = session;
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugPopped, setDebugPopped] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const cartRef = useRef<HTMLDivElement>(null);
  const connected = state.connection === 'connected';

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
      if (!typing && (e.key === 'd' || e.key === 'D')) setDebugOpen((open) => !open);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const onCartClick = useCallback(() => {
    if (window.matchMedia('(min-width: 1180px)').matches) {
      cartRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      cartRef.current?.classList.add('flash');
      setTimeout(() => cartRef.current?.classList.remove('flash'), 900);
    } else {
      setDrawerOpen(true);
    }
  }, []);

  const sidebar = (
    <>
      <AssistantPanel conversation={state.conversation} onSuggestion={session.sendText} disabled={!connected}
        question={state.question} listening={state.mic === 'on'} />
      <div ref={cartRef}>
        <CartPanel {...state.cart} onCheckout={() => session.sendText('Place my order')} onRemove={session.removeFromCart}
          disabled={!connected} />
      </div>
    </>
  );

  return (
    <div className="app">
      <Header
        connection={state.connection}
        status={state.status}
        mic={state.mic}
        micLevel={state.micLevel}
        cartCount={state.cart.itemCount}
        voiceReplies={state.voiceReplies}
        onVoiceRepliesChange={session.setVoiceReplies}
        userName={userName}
        onMicToggle={state.mic === 'on' ? session.stopMic : session.startMic}
        onSendText={session.sendText}
        onCartClick={onCartClick}
        onSignOut={onSignOut}
      />

      {state.error && (
        <div className="toast" role="alert">
          <InfoIcon size={18} />
          <span>{state.error}</span>
          <button className="icon-button" onClick={session.dismissError} aria-label="Dismiss"><CloseIcon size={16} /></button>
        </div>
      )}

      <main className="layout">
        <div className="layout__main">
          <ChipBar
            chips={state.chips}
            remembered={state.remembered}
            counts={state.counts}
            shown={state.shelf.length}
            tentative={state.shelfTentative}
            onRemove={session.removeChip}
          />
          {state.status?.kind === 'no_match' && (
            <div className="notice notice--warning" role="status">
              <InfoIcon size={18} /> {state.status.message}
            </div>
          )}
          {state.status?.kind === 'clarifying' && (
            <div className="notice notice--info" role="status">
              <InfoIcon size={18} /> {state.status.message}
            </div>
          )}
          <ShelfGrid
            items={state.shelf}
            tentative={state.shelfTentative}
            loading={state.status?.kind === 'thinking'}
            received={state.shelfReceived}
            onOpen={session.tapProduct}
            onColumns={session.setColumns}
          />
        </div>
        <aside className="layout__side">{sidebar}</aside>
      </main>

      <Footer onToggleDebug={() => setDebugOpen((open) => !open)} />

      {drawerOpen && (
        <div className="sheet-backdrop" onClick={() => setDrawerOpen(false)}>
          <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label="Assistant and cart">
            <button className="sheet__close icon-button" onClick={() => setDrawerOpen(false)} aria-label="Close"><CloseIcon /></button>
            {sidebar}
          </aside>
        </div>
      )}
      {state.product && (
        <ProductDetail
          product={state.product.detail}
          why={state.product.why}
          onClose={session.closeProduct}
          onAddToCart={() => session.sendText('Add it to my cart')}
        />
      )}
      {state.order && <OrderConfirmation order={state.order} onClose={session.dismissOrder} />}
      {debugOpen && !debugPopped && (
        <DebugOverlay turns={state.debug} activity={state.activity} onClose={() => setDebugOpen(false)}
          onPopOut={() => setDebugPopped(true)} />
      )}
      {debugPopped && (
        <PopoutWindow title="VoiceShop: behind the scenes" onClose={() => { setDebugPopped(false); setDebugOpen(false); }}>
          <DebugOverlay turns={state.debug} activity={state.activity} poppedOut
            onClose={() => { setDebugPopped(false); setDebugOpen(false); }} />
        </PopoutWindow>
      )}
    </div>
  );
}
