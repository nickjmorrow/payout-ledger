import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from 'src/App';
import ErrorBoundary from 'src/components/ErrorBoundary';
import 'src/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // On, because this tab is not the only writer: the worker settles
      // payments in the background and another operator may be disbursing.
      // The event stream carries most of that; this covers a tab that was
      // asleep while it happened.
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
