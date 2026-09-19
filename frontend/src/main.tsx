import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from 'src/App';
import ErrorBoundary from 'src/components/ErrorBoundary';
import 'src/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // On, because this tab is no longer the only writer: a scheduled agent
      // run creates conversations on its own, and without this you would not
      // see one until you reloaded. This is the line the comment here used to
      // tell you to change once something else could write. Something can.
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
