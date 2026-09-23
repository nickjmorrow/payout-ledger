import { type RefObject, useEffect } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Keep keyboard focus inside a modal while it is open, and give it back after.
 *
 * On open, focus moves to the first thing in the container, so a keyboard user
 * lands in the dialog they just opened rather than on the row behind it. Tab
 * and Shift+Tab wrap at the ends. On close, focus returns to whatever opened
 * it, so the next Tab continues from where they were.
 */
export default function useFocusTrap(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const container = ref.current;
    if (!container) return;

    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
    focusable()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return;
      const items = focusable();
      const first = items[0];
      const last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      opener?.focus();
    };
  }, [ref]);
}
