import { useSyncExternalStore } from 'react';
import { getLiveStatus, type LiveStatus, subscribeLiveStatus } from 'src/api/events';

/** Whether the console is hearing changes as they happen. See `api/events.ts`. */
export default function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(subscribeLiveStatus, getLiveStatus);
}
