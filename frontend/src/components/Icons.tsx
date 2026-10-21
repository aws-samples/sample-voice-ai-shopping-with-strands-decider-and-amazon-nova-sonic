/** Inline SVG icons (stroke icons share one style; sized by the caller's font size). */
import type { ReactNode } from 'react';

type IconProps = { size?: number; className?: string; title?: string };

function Svg({ size = 20, className, title, children }: IconProps & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden={title ? undefined : true}
      role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

export const MicIcon = (p: IconProps) => (
  <Svg {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Svg>
);
export const MicOffIcon = (p: IconProps) => (
  <Svg {...p}><path d="M15 9.3V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 5 2.2M5 11a7 7 0 0 0 11.3 5.5M19 11a7 7 0 0 1-.6 2.8M12 18v3M3 3l18 18" /></Svg>
);
export const CartIcon = (p: IconProps) => (
  <Svg {...p}><path d="M3 4h2l2.4 11.2a1.5 1.5 0 0 0 1.5 1.2h8.6a1.5 1.5 0 0 0 1.5-1.1L21 8H6.2" /><circle cx="9.5" cy="20" r="1.2" /><circle cx="17.5" cy="20" r="1.2" /></Svg>
);
export const SpeakerIcon = (p: IconProps) => (
  <Svg {...p}><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /></Svg>
);
export const SpeakerOffIcon = (p: IconProps) => (
  <Svg {...p}><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" /><path d="M16 9.5l5 5M21 9.5l-5 5" /></Svg>
);
export const CloseIcon = (p: IconProps) => <Svg {...p}><path d="M6 6l12 12M18 6L6 18" /></Svg>;
export const SendIcon = (p: IconProps) => <Svg {...p}><path d="M4 12l16-8-6 16-2.5-6.5L4 12z" /></Svg>;
export const SparkIcon = (p: IconProps) => (
  <Svg {...p}><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16z" /></Svg>
);
export const BugIcon = (p: IconProps) => (
  <Svg {...p}><rect x="8" y="7" width="8" height="12" rx="4" /><path d="M12 7V5M9 4l1.5 2M15 4l-1.5 2M4 11h4M16 11h4M4 17h4M16 17h4M12 11v8" /></Svg>
);
export const CheckIcon = (p: IconProps) => <Svg {...p}><path d="M5 12.5l4.5 4.5L19 7.5" /></Svg>;
export const UserIcon = (p: IconProps) => <Svg {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></Svg>;
export const InfoIcon = (p: IconProps) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.5" /></Svg>;
export const BagIcon = (p: IconProps) => (
  <Svg {...p}><path d="M5 8h14l-1 12H6L5 8z" /><path d="M9 8V6a3 3 0 0 1 6 0v2" /></Svg>
);

export function Stars({ rating, size = 14 }: { rating: number; size?: number }) {
  const full = Math.round(rating * 2) / 2;
  return (
    <span className="stars" aria-label={`${rating.toFixed(1)} out of 5 stars`}>
      {[0, 1, 2, 3, 4].map((i) => {
        const fill = full >= i + 1 ? 1 : full >= i + 0.5 ? 0.5 : 0;
        return (
          <svg key={i} width={size} height={size} viewBox="0 0 24 24" aria-hidden>
            <defs>
              <linearGradient id={`star-${i}-${fill}`}>
                <stop offset={`${fill * 100}%`} stopColor="currentColor" />
                <stop offset={`${fill * 100}%`} stopColor="var(--star-empty)" />
              </linearGradient>
            </defs>
            <path fill={`url(#star-${i}-${fill})`} d="M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8L12 2.8z" />
          </svg>
        );
      })}
    </span>
  );
}
