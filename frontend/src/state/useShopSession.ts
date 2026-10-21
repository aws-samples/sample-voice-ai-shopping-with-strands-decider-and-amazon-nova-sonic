/**
 * Session state for the shopping page: one reducer over the session host's
 * messages, plus the actions the UI sends back.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react';
import { MicrophoneCapture, SpeechPlayer, base64FromBuffer, bufferFromBase64 } from '../audio/AudioIO';
import type {
  Activity, CartLine, Chip, ConnectionState, DebugQuestion, DebugTimings, HostMessage, ProductDetail, SessionTransport,
  ShelfItem, Slot, StatusKind,
} from '../services/protocol';

export interface ConversationEntry {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  final: boolean;
}

export interface DebugTurn {
  turnId: number;
  transcript: string;
  questions: DebugQuestion[];
  counts: Record<string, number>;
  timings: DebugTimings;
  backendNotes?: string;
}

export interface OrderConfirmation {
  orderId: string;
  itemCount: number;
  totalCents: number;
  placedAt: string;
}

export type MicState = 'off' | 'starting' | 'on' | 'denied' | 'unsupported';

export interface ShopState {
  connection: ConnectionState;
  chips: Chip[];
  remembered: string[];
  shelf: ShelfItem[];
  shelfTentative: boolean;
  shelfReceived: boolean;   // false until the host sends the first shelf (skeletons until then)
  counts: { catalog: number; afterFilters: number; candidates: number } | null;
  product: { detail: ProductDetail; why: string } | null;
  cart: { items: CartLine[]; itemCount: number; totalCents: number };
  order: OrderConfirmation | null;
  status: { kind: StatusKind; message: string } | null;
  conversation: ConversationEntry[];
  debug: DebugTurn[];
  /** Backend activity per turn, in arrival order (the debug overlay's timeline). */
  activity: Record<number, Activity[]>;
  /** The pending yes/no question, answered by voice, typing or its buttons. */
  question: { turnId: number; text: string } | null;
  error: string | null;
  mic: MicState;
  micLevel: number;
  voiceReplies: boolean;
}

const VOICE_REPLIES_KEY = 'voiceshop.voiceReplies';

const initialState: ShopState = {
  connection: 'connecting',
  chips: [],
  remembered: [],
  shelf: [],
  shelfTentative: false,
  shelfReceived: false,
  counts: null,
  product: null,
  cart: { items: [], itemCount: 0, totalCents: 0 },
  order: null,
  status: null,
  conversation: [],
  debug: [],
  activity: {},
  question: null,
  error: null,
  mic: 'off',
  micLevel: 0,
  voiceReplies: localStorage.getItem(VOICE_REPLIES_KEY) === 'on',
};

type Action =
  | { kind: 'host'; message: HostMessage }
  | { kind: 'connection'; state: ConnectionState }
  | { kind: 'mic'; state: MicState }
  | { kind: 'micLevel'; level: number }
  | { kind: 'voiceReplies'; enabled: boolean }
  | { kind: 'closeProduct' }
  | { kind: 'dismissOrder' }
  | { kind: 'dismissError' };

let entryId = 0;
const MAX_CONVERSATION = 50;
const MAX_DEBUG_TURNS = 30;

function addTranscript(conversation: ConversationEntry[], role: 'user' | 'assistant', text: string, final: boolean): ConversationEntry[] {
  const last = conversation[conversation.length - 1];
  // Streaming assistant text arrives as partial chunks followed by a final copy:
  // grow the open entry, and treat the final copy as closing it (no duplicate).
  if (last && last.role === role && !last.final) {
    const merged = final ? { ...last, final: true, text: text.length >= last.text.length ? text : last.text }
      : { ...last, text: role === 'assistant' ? last.text + text : text };
    return [...conversation.slice(0, -1), merged];
  }
  return [...conversation, { id: ++entryId, role, text, final }].slice(-MAX_CONVERSATION);
}

function reducer(state: ShopState, action: Action): ShopState {
  switch (action.kind) {
    case 'connection':
      return { ...state, connection: action.state };
    case 'mic':
      return { ...state, mic: action.state, micLevel: action.state === 'on' ? state.micLevel : 0 };
    case 'micLevel':
      return { ...state, micLevel: action.level };
    case 'voiceReplies':
      return { ...state, voiceReplies: action.enabled };
    case 'closeProduct':
      return { ...state, product: null };
    case 'dismissOrder':
      return { ...state, order: null };
    case 'dismissError':
      return { ...state, error: null };
    case 'host':
      break;
  }
  const m = action.message;
  switch (m.type) {
    case 'ui.chips':
      return { ...state, chips: m.chips, remembered: m.remembered };
    case 'ui.shelf':
      // Exactly what the host has: an empty shelf means nothing passed the filters.
      return { ...state, shelf: m.items, shelfTentative: m.tentative, counts: m.counts, shelfReceived: true };
    case 'ui.question':
      return { ...state, question: { turnId: m.turnId, text: m.text } };
    case 'ui.shelf_append':
      return { ...state, shelf: [...state.shelf, ...m.items] };
    case 'ui.product_open':
      return { ...state, product: { detail: m.product, why: m.why } };
    case 'ui.product_close':
      return { ...state, product: null };
    case 'ui.cart':
      return { ...state, cart: { items: m.items, itemCount: m.itemCount, totalCents: m.totalCents } };
    case 'ui.order_placed':
      return { ...state, order: { orderId: m.orderId, itemCount: m.itemCount, totalCents: m.totalCents, placedAt: m.placedAt }, product: null };
    case 'ui.status':
      return { ...state, status: { kind: m.kind, message: m.message } };
    case 'ui.debug':
      return { ...state, debug: [...state.debug, { ...m }].slice(-MAX_DEBUG_TURNS) };
    case 'ui.activity': {
      const { type: _type, ...entry } = m;
      const turns = { ...state.activity, [m.turnId]: [...(state.activity[m.turnId] ?? []), entry] };
      const keep = Object.keys(turns).map(Number).sort((a, b) => b - a).slice(0, MAX_DEBUG_TURNS);
      return { ...state, activity: Object.fromEntries(keep.map((k) => [k, turns[k]])) };
    }
    case 'bidi_transcript_stream': {
      const text = m.text ?? m.transcript ?? '';
      if (!text) return state;
      if ((m.role ?? 'assistant') === 'user' && m.is_final) state = { ...state, question: null };   // answered
      // Voice off: Sonic only says "Here you go." / "Take a look."; the chat shows the app's reply (ui.reply).
      if ((m.role ?? 'assistant') === 'assistant' && !state.voiceReplies) return state;
      return { ...state, conversation: addTranscript(state.conversation, m.role ?? 'assistant', text, m.is_final ?? false) };
    }
    case 'ui.reply':
      if (state.voiceReplies) return state;   // voice on: the chat shows what Sonic says
      return { ...state, conversation: addTranscript(state.conversation, 'assistant', m.text, true) };
    case 'error':
      return { ...state, error: m.message };
    default:
      return state;
  }
}

export function useShopSession(transport: SessionTransport) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const player = useRef(new SpeechPlayer());
  const mic = useRef<MicrophoneCapture | null>(null);
  const voiceReplies = useRef(state.voiceReplies);
  const columns = useRef<number | null>(null);
  const listenForAnswer = useRef<() => void>(() => undefined);   // set below, once startMic exists   // re-sent on every connect: a layout sent earlier is lost

  useEffect(() => {
    transport.onConnectionChange((s) => {
      dispatch({ kind: 'connection', state: s });
      if (s === 'connected') {
        transport.send({ type: 'ui.voice_replies', enabled: voiceReplies.current });
        if (columns.current) transport.send({ type: 'ui.layout', columns: columns.current });
      }
    });
    transport.onMessage((message) => {
      if (message.type === 'bidi_audio_stream') {
        // Visual-first (ADR-23): the session host only sends audio when spoken replies
        // are on, or for a yes/no confirmation, so everything that arrives is played.
        player.current.play(bufferFromBase64(message.audio));
        return;
      }
      if (message.type === 'bidi_interruption') return player.current.flush();
      if (message.type === 'ui.question' && !mic.current) listenForAnswer.current();
      if (message.type === 'ui.scroll') {
        // A view change only: the page moves, the state does not.
        // The product area scrolls (the header and footer are fixed); the window on small screens.
        const area = document.querySelector<HTMLElement>('.layout__main');
        const target: HTMLElement | Window = area && area.scrollHeight > area.clientHeight ? area : window;
        const step = (area?.clientHeight ?? window.innerHeight) * 0.8;
        if (message.direction === 'top') target.scrollTo({ top: 0, behavior: 'smooth' });
        else target.scrollBy({ top: message.direction === 'down' ? step : -step, behavior: 'smooth' });
        return;
      }
      dispatch({ kind: 'host', message });
    });
    transport.connect().catch((err) => (transport as { failed?: (e: unknown) => void }).failed?.(err));
    return () => {
      mic.current?.stop();
      transport.disconnect();
    };
  }, [transport]);

  const sendText = useCallback((text: string) => {
    const trimmed = text.trim().slice(0, 500);
    player.current.unlock();   // inside the shopper's action, so the browser lets replies play
    if (trimmed) transport.send({ type: 'bidi_text_input', text: trimmed });
  }, [transport]);

  const startMic = useCallback(async () => {
    dispatch({ kind: 'mic', state: 'starting' });
    player.current.unlock();
    try {
      const capture = new MicrophoneCapture();
      await capture.start(
        (pcm) => transport.send({ type: 'bidi_audio_input', audio: base64FromBuffer(pcm), format: 'pcm', sample_rate: 16000, channels: 1 }),
        (level) => dispatch({ kind: 'micLevel', level }),
      );
      mic.current = capture;
      dispatch({ kind: 'mic', state: 'on' });
    } catch (err) {
      dispatch({ kind: 'mic', state: (err as DOMException).name === 'NotAllowedError' ? 'denied' : 'off' });
    }
  }, [transport]);

  // A yes/no question opens the mic so it can be answered by voice, but only when the
  // browser already allows the microphone for this site: no permission prompt appears
  // without a click. The mic stays on afterwards (design decision 2026-10-20).
  listenForAnswer.current = () => {
    const permissions = navigator.permissions as Permissions | undefined;
    permissions?.query({ name: 'microphone' as PermissionName })
      .then((p) => { if (p.state === 'granted' && !mic.current) void startMic(); })
      .catch(() => undefined);   // browsers without this query: the Yes / No buttons remain
  };

  const stopMic = useCallback(() => {
    mic.current?.stop();
    mic.current = null;
    dispatch({ kind: 'mic', state: 'off' });
  }, []);

  return {
    state,
    sendText,
    startMic,
    stopMic,
    removeChip: useCallback((slot: Slot) => transport.send({ type: 'ui.chip_remove', slot }), [transport]),
    tapProduct: useCallback((asin: string) => transport.send({ type: 'ui.product_tap', asin }), [transport]),
    removeFromCart: useCallback((asin: string) => transport.send({ type: 'ui.cart_remove', asin }), [transport]),
    closeProduct: useCallback(() => {
      transport.send({ type: 'ui.close_product' });
      dispatch({ kind: 'closeProduct' });
    }, [transport]),
    setColumns: useCallback((n: number) => {
      columns.current = n;
      transport.send({ type: 'ui.layout', columns: n });
    }, [transport]),
    setVoiceReplies: useCallback((enabled: boolean) => {
      voiceReplies.current = enabled;
      localStorage.setItem(VOICE_REPLIES_KEY, enabled ? 'on' : 'off');
      if (!enabled) player.current.flush();
      transport.send({ type: 'ui.voice_replies', enabled });
      dispatch({ kind: 'voiceReplies', enabled });
    }, [transport]),
    dismissOrder: useCallback(() => dispatch({ kind: 'dismissOrder' }), []),
    dismissError: useCallback(() => dispatch({ kind: 'dismissError' }), []),
  };
}
