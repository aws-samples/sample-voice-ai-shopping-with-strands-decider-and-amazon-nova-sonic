import { useState, type FormEvent } from 'react';
import type { MicState } from '../state/useShopSession';
import type { ConnectionState, StatusKind } from '../services/protocol';
import { BagIcon, CartIcon, MicIcon, MicOffIcon, SendIcon, SpeakerIcon, SpeakerOffIcon, UserIcon } from './Icons';

interface HeaderProps {
  connection: ConnectionState;
  status: { kind: StatusKind; message: string } | null;
  mic: MicState;
  micLevel: number;
  cartCount: number;
  voiceReplies: boolean;
  onVoiceRepliesChange: (enabled: boolean) => void;
  userName?: string;
  onMicToggle: () => void;
  onSendText: (text: string) => void;
  onCartClick: () => void;
  onSignOut?: () => void;
}

const STATUS_LABEL: Record<StatusKind | 'ready', string> = {
  listening: 'Listening',
  ready: 'Ready',
  thinking: 'Thinking',
  clarifying: 'Question for you',
  no_match: 'No match',
  continuation: 'Reconnecting voice',
  degraded: 'Limited',
  error: 'Error',
};

export function Header(props: HeaderProps) {
  const [draft, setDraft] = useState('');
  const { connection, status, mic } = props;
  const live = mic === 'on';
  // "Listening" only when the mic is actually on; otherwise the store is ready for typing.
  const kind = status?.kind === 'listening' && !live ? 'ready' : status?.kind;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    props.onSendText(draft);
    setDraft('');
  };

  const hint = connection !== 'connected'
    ? connection === 'reconnecting' ? 'Reconnecting to the store…' : connection === 'connecting' ? 'Connecting to the store…' : 'Disconnected'
    : mic === 'denied' ? 'Microphone access is blocked. Allow it in your browser settings to shop by voice.'
      : mic === 'unsupported' ? 'Voice input is not supported in this browser. Type your request instead.'
        : status && kind !== 'ready' && status.message !== STATUS_LABEL[status.kind] ? status.message
          : live ? 'Go ahead, I am listening' : 'Type below, or tap the mic and say what you are looking for';

  return (
    <header className="site-header">
      <div className="site-header__inner">
        <a className="brand" href="/" aria-label="VoiceShop home">
          <span className="brand__mark"><BagIcon size={18} /></span>
          <span className="brand__text">
            <span className="brand__name">VoiceShop</span>
            <span className="brand__tag">Shop by voice</span>
          </span>
        </a>

        <form className={`voice-bar voice-bar--${kind ?? 'idle'}`} onSubmit={submit} role="search">
          <button
            type="button"
            className={`mic-button${live ? ' mic-button--live' : ''}`}
            onClick={props.onMicToggle}
            disabled={(!live && connection !== 'connected') || mic === 'starting'}
            aria-pressed={live}
            aria-label={live ? 'Stop listening' : 'Start talking'}
            style={live ? { boxShadow: `0 0 0 ${4 + Math.round(props.micLevel * 10)}px rgba(240, 82, 82, 0.18)` } : undefined}
          >
            {mic === 'denied' ? <MicOffIcon size={20} /> : <MicIcon size={20} />}
          </button>
          <div className="voice-bar__body">
            <div className="voice-bar__status" aria-live="polite">
              {kind && connection === 'connected' && (
                <span className={`status-dot status-dot--${kind}`}>{STATUS_LABEL[kind]}</span>
              )}
              <span className="voice-bar__hint">{hint}</span>
            </div>
            <input
              className="voice-bar__input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder='Or type, e.g. "a leather bag for my mom under $80"'
              maxLength={500}
              aria-label="Type a request"
              disabled={connection !== 'connected'}
            />
          </div>
          <button type="submit" className="icon-button" aria-label="Send" disabled={!draft.trim() || connection !== 'connected'}>
            <SendIcon size={18} />
          </button>
          <button
            type="button"
            className={`voice-toggle${props.voiceReplies ? ' voice-toggle--on' : ''}`}
            role="switch"
            aria-checked={props.voiceReplies}
            onClick={() => props.onVoiceRepliesChange(!props.voiceReplies)}
            title={props.voiceReplies ? 'Spoken replies on: the assistant talks back' : 'Spoken replies off: answers appear on screen only'}
          >
            {props.voiceReplies ? <SpeakerIcon size={18} /> : <SpeakerOffIcon size={18} />}
            <span className="voice-toggle__label">{props.voiceReplies ? 'Voice on' : 'Voice off'}</span>
          </button>
        </form>

        <div className="header-actions">
          <button className="cart-button" onClick={props.onCartClick} aria-label={`Cart, ${props.cartCount} items`}>
            <CartIcon size={22} />
            {props.cartCount > 0 && <span className="cart-button__count">{props.cartCount}</span>}
          </button>
          {props.onSignOut && (
            <button className="user-button" onClick={props.onSignOut} title="Sign out">
              <UserIcon size={18} />
              <span>{props.userName ?? 'Sign out'}</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
