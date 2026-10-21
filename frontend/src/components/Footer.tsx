import { BugIcon } from './Icons';

export function Footer({ onToggleDebug }: { onToggleDebug: () => void }) {
  return (
    <footer className="site-footer">
      <div className="site-footer__inner">
        <span className="site-footer__text" title="VoiceShop is a sample application. No payment is ever taken. Every product, brand and image is generated.">
          <strong>VoiceShop</strong> sample: Amazon Nova Sonic for speech, Strands Decider for decisions, Amazon Bedrock
          AgentCore for hosting. No payment is ever taken; every product, brand and image is generated.
        </span>
        <button className="link-button" onClick={onToggleDebug}><BugIcon size={14} /> Debug overlay</button>
      </div>
    </footer>
  );
}
