import { apiFetch } from 'src/api/client';

export interface Health {
  database: string;
  status: string;
}

export function getHealth(): Promise<Health> {
  return apiFetch<Health>('/health');
}
