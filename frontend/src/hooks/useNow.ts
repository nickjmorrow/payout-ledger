import { useEffect, useState } from 'react';

/**
 * The current time, refreshed every `intervalMs`, for countdowns. Formatting stays
 * in pure functions like `formatRelative`.
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
