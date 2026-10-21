/**
 * The browser <-> session host WebSocket contract. The session host speaks it.
 */

export type Slot = 'recipient' | 'gender' | 'category' | 'budget';

export interface Chip {
  slot: Slot;
  label: string;
  value: string;
  confidence: number;
}

export interface ShelfItem {
  asin: string;
  title: string;
  brand: string;
  priceCents: number;
  rating: number;
  imageUrl: string;
}

export interface ProductDetail {
  asin: string;
  title: string;
  brand: string;
  category: string;
  color?: string;
  material?: string;
  size?: string | null;
  priceCents: number;
  averageRating?: number;
  ratingNumber?: number;
  features: string[];
  description: string;
  available?: boolean;
  images: { largeUrl: string | null };
}

export interface CartLine {
  asin: string;
  title: string;
  quantity: number;
  priceCents: number;
}

export type StatusKind = 'listening' | 'thinking' | 'clarifying' | 'no_match' | 'continuation' | 'degraded' | 'error';

export interface DebugQuestion {
  id: string;
  /** The question in plain words ("Who is it for?"). */
  label?: string;
  type: string;
  top: { option: string; name?: string; p: number }[];
}

export type ActivityService = 'sonic' | 'decider' | 'search' | 'dynamodb' | 'memory' | 'app';

/** One backend step in plain words (Nova Sonic, Strands Decider, search, DynamoDB, memory, a decision). */
export interface Activity {
  turnId: number;
  at: number;
  service: ActivityService;
  title: string;
  detail?: string;
  ms?: number | null;
}

export interface DebugTimings {
  sonicFinalMs?: number;
  understandMs?: number;
  retrievalMs?: number;
  chooseMs?: number;
  firstUiMs?: number;
  shelfMs?: number;
}

/** Host -> browser messages this UI handles (Sonic events plus the ui.* events). */
export type HostMessage =
  | { type: 'ui.chips'; turnId: number; chips: Chip[]; remembered: string[] }
  | { type: 'ui.shelf'; turnId: number; tentative: boolean; items: ShelfItem[]; counts: { catalog: number; afterFilters: number; candidates: number } }
  /** The rest of a shelf over twelve items, in chunks (the WebSocket allows 64 KB per message). */
  | { type: 'ui.shelf_append'; turnId: number; items: ShelfItem[] }
  /** A yes/no question is pending (Yes / No buttons; the mic opens when already permitted). */
  | { type: 'ui.question'; turnId: number; text: string }
  /** The app's reply for the turn; the chat shows it while spoken replies are off. */
  | { type: 'ui.reply'; turnId: number; text: string }
  | { type: 'ui.product_open'; turnId: number; product: ProductDetail; why: string; confidence?: number }
  | { type: 'ui.product_close'; turnId: number }
  | { type: 'ui.scroll'; turnId: number; direction: 'down' | 'up' | 'top' }
  | { type: 'ui.cart'; items: CartLine[]; itemCount: number; totalCents: number }
  | { type: 'ui.order_placed'; turnId: number; orderId: string; itemCount: number; totalCents: number; placedAt: string }
  | { type: 'ui.status'; kind: StatusKind; message: string }
  | ({ type: 'ui.activity' } & Activity)
  | { type: 'ui.debug'; turnId: number; transcript: string; questions: DebugQuestion[]; counts: Record<string, number>; timings: DebugTimings; backendNotes?: string }
  | { type: 'bidi_transcript_stream'; role: 'user' | 'assistant'; text?: string; transcript?: string; is_final?: boolean }
  | { type: 'bidi_audio_stream'; audio: string }
  | { type: 'bidi_interruption' }
  | { type: 'error'; message: string };

/** Browser -> host messages. */
export type BrowserMessage =
  | { type: 'auth'; access_token: string }
  | { type: 'bidi_audio_input'; audio: string; format: 'pcm'; sample_rate: 16000; channels: 1 }
  | { type: 'bidi_text_input'; text: string }
  | { type: 'ui.layout'; columns: number }
  | { type: 'ui.chip_remove'; slot: Slot }
  | { type: 'ui.product_tap'; asin: string }
  | { type: 'ui.close_product' }
  | { type: 'ui.cart_remove'; asin: string }
  /** Visual-first (ADR-23): spoken replies are off unless the shopper turns them on. */
  | { type: 'ui.voice_replies'; enabled: boolean };

/** What the UI needs from a session (the AgentCore WebSocket). */
export interface SessionTransport {
  connect(): Promise<void>;
  send(message: BrowserMessage): void;
  disconnect(): void;
  onMessage(handler: (message: HostMessage) => void): void;
  onConnectionChange(handler: (state: ConnectionState) => void): void;
}

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export const formatPrice = (cents: number): string =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100);
