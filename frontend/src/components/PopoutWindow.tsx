import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * Renders its children in a separate browser window (for example the activity
 * overlay, kept next to the shop while recording a demo). The children stay part
 * of this page's React tree, so they update live from the same session; nothing
 * connects twice. Closing the window calls onClose.
 */
export function PopoutWindow({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const [container, setContainer] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const popup = window.open('', 'voiceshop-activity', 'popup,width=700,height=900');
    if (!popup) {   // blocked by the browser: stay in the page
      onClose();
      return;
    }
    popup.document.title = title;
    popup.document.body.innerHTML = '';
    // The page's styles (Vite injects them as <style> and <link> elements).
    for (const node of document.querySelectorAll('style, link[rel="stylesheet"]')) {
      const copy = node.cloneNode(true) as HTMLElement;
      if (node instanceof HTMLLinkElement) (copy as HTMLLinkElement).href = node.href;   // absolute: the popup is about:blank
      popup.document.head.appendChild(copy);
    }
    popup.document.body.className = 'popout';
    const root = popup.document.createElement('div');
    popup.document.body.appendChild(root);
    setContainer(root);
    const closed = () => onClose();
    popup.addEventListener('pagehide', closed);
    const closeWithPage = () => popup.close();
    window.addEventListener('pagehide', closeWithPage);
    return () => {
      popup.removeEventListener('pagehide', closed);
      window.removeEventListener('pagehide', closeWithPage);
      popup.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return container ? createPortal(children, container) : null;
}
