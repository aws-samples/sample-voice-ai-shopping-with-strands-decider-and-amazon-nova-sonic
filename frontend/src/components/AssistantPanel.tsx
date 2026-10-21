import { useEffect, useRef } from 'react';
import type { ConversationEntry } from '../state/useShopSession';
import { SparkIcon } from './Icons';

const SUGGESTIONS = [
  'A gift for my mom under $80',
  'Show me something in purple',
  'The second one',
  'Add it to my cart',
  'Place my order',
];

export function AssistantPanel({ conversation, onSuggestion, disabled, question, listening }: {
  conversation: ConversationEntry[];
  onSuggestion: (text: string) => void;
  disabled: boolean;
  /** A pending yes/no question: answer by voice, typing or these buttons. */
  question?: { text: string } | null;
  listening?: boolean;
}) {
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Scroll the conversation log only: scrollIntoView also scrolls the page, which
    // undid a voice "scroll down" as soon as Sonic's reply text arrived.
    const log = end.current?.parentElement;
    if (log) log.scrollTop = log.scrollHeight;
  }, [conversation, question]);

  return (
    <section className="panel assistant" aria-label="Conversation">
      <header className="panel__header">
        <span className="panel__title"><SparkIcon size={16} /> Assistant</span>
      </header>
      <div className="assistant__log" aria-live="polite">
        {conversation.length === 0 && <p className="muted assistant__empty">Your conversation appears here.</p>}
        {conversation.map((entry) => (
          <div key={entry.id} className={`bubble bubble--${entry.role}`}>{entry.text}</div>
        ))}
        {question && (
          <div className="answer" role="group" aria-label={question.text}>
            <button className="answer__button answer__button--yes" onClick={() => onSuggestion('Yes')} disabled={disabled}>Yes</button>
            <button className="answer__button" onClick={() => onSuggestion('No')} disabled={disabled}>No</button>
            <span className="answer__hint muted">{listening ? 'or just say it' : 'or type your answer'}</span>
          </div>
        )}
        <div ref={end} />
      </div>
      <div className="assistant__suggestions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="suggestion" onClick={() => onSuggestion(s)} disabled={disabled}>{s}</button>
        ))}
      </div>
    </section>
  );
}
