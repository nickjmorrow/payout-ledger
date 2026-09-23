import { useEffect, useState } from 'react';

/**
 * The time, re-read every `intervalMs`, for anything that counts down.
 *
 * A clock is something outside React that changes on its own, which is what
 * an effect is for. The consumer passes the result to a pure formatter
 * (`formatRelative`) rather than reading `Date.now()` during render, so the
 * formatting stays testable and every countdown on screen agrees.
 */
export default function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => {
      setNow(Date.now());
    }, intervalMs);
    return () => {
      clearInterval(timer);
    };
  }, [intervalMs]);

  return now;
}
