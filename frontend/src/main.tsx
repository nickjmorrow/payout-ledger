import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from 'src/App';
import ErrorBoundary from 'src/components/ErrorBoundary';
import 'src/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The worker and other operators change data too; this catches a tab that slept.
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

const root = document.getElementById('root');
if (!root) throw new Error('#root not found');

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </QueryClientProvider>
  </StrictMode>,
);
