import type { Activity, ActivityService } from '../services/protocol';
import type { DebugTurn } from '../state/useShopSession';
import { CloseIcon } from './Icons';

const TIMING_LABELS: [keyof DebugTurn['timings'], string][] = [
  ['understandMs', 'Understanding the sentence'],
  ['retrievalMs', 'Catalog search'],
  ['chooseMs', 'Ranking products'],
  ['firstUiMs', 'Chips on screen'],
  ['shelfMs', 'Shelf on screen'],
];

const SERVICE_NAMES: Record<ActivityService, string> = {
  sonic: 'Nova Sonic',
  decider: 'Strands Decider 2B',
  search: 'Search',
  dynamodb: 'DynamoDB',
  memory: 'Memory',
  app: 'App',
};

interface Props {
  turns: DebugTurn[];
  activity: Record<number, Activity[]>;
  onClose: () => void;
  /** In the page: open it in its own window. Absent when it already is one. */
  onPopOut?: () => void;
  poppedOut?: boolean;
}

/**
 * What happens in the backend for each sentence, in plain words: Nova Sonic hearing
 * and speaking, each Strands Decider call with its answers, the catalog search, DynamoDB
 * reads and writes, memory saves and the decision the app made. Toggle with D.
 */
export function DebugOverlay({ turns, activity, onClose, onPopOut, poppedOut = false }: Props) {
  const byTurn = new Map(turns.map((t) => [t.turnId, t]));
  const ids = [...new Set([...Object.keys(activity).map(Number), ...byTurn.keys()])].sort((a, b) => b - a);
  return (
    <aside className={`debug${poppedOut ? ' debug--window' : ''}`} aria-label="Backend activity">
      <header className="debug__header">
        <strong>Behind the scenes</strong>
        <span className="muted">{poppedOut ? 'VoiceShop backend, live' : 'press D to toggle'}</span>
        {onPopOut && <button className="debug__popout" onClick={onPopOut}>Pop out ↗</button>}
        <button className="icon-button" onClick={onClose} aria-label="Close"><CloseIcon size={16} /></button>
      </header>
      <div className="debug__body">
        {ids.length === 0 && <p className="muted">Say or type something to see what happens behind the scenes.</p>}
        {ids.map((id) => {
          const steps = activity[id] ?? [];
          const turn = byTurn.get(id);
          const said = turn?.transcript ?? steps.find((s) => s.service === 'sonic' || s.title === 'You typed')?.detail?.replace(/^"|"$/g, '');
          const start = steps.length ? steps[0].at : 0;
          return (
            <article key={id} className="debug__turn">
              <div className="debug__transcript">{id === 0 ? 'Opening the shop' : <>“{said ?? '…'}”</>}</div>
              <ol className="debug__steps">
                {steps.map((step, index) => (
                  <li key={index} className="debug__step">
                    <span className="debug__time">+{((step.at - start) / 1000).toFixed(1)}s</span>
                    <span className={`debug__badge debug__badge--${step.service}`}>{SERVICE_NAMES[step.service] ?? step.service}</span>
                    <span className="debug__what">
                      <span className="debug__title">{step.title}{step.ms != null && <span className="muted"> · {step.ms} ms</span>}</span>
                      {step.detail && <span className="debug__detail">{step.detail}</span>}
                    </span>
                  </li>
                ))}
              </ol>
              {turn && (
                <details className="debug__more">
                  <summary>Timings and every answer</summary>
                  <div className="debug__timings">
                    {TIMING_LABELS.filter(([k]) => turn.timings[k] !== undefined).map(([k, label]) => (
                      <span key={k} className="debug__timing"><span>{label}</span><strong>{turn.timings[k]} ms</strong></span>
                    ))}
                  </div>
                  <table className="debug__questions">
                    <tbody>
                      {turn.questions.map((q) => (
                        <tr key={q.id}>
                          <th>{q.label ?? q.id}</th>
                          <td>{q.top.map((o) => `${o.name ?? o.option} ${Math.round(o.p * 100)}%`).join(' · ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {turn.backendNotes && <div className="debug__notes">{turn.backendNotes}</div>}
                  {Object.keys(turn.counts).length > 0 && (
                    <div className="debug__counts muted">
                      {Object.entries(turn.counts).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' → ')}
                    </div>
                  )}
                </details>
              )}
            </article>
          );
        })}
      </div>
    </aside>
  );
}
