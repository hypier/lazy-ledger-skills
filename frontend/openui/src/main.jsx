import React from 'react';
import { createRoot } from 'react-dom/client';
import { App, validateView, showFallback } from './view.jsx';
try {
  const bytes = Uint8Array.from(atob(document.getElementById('ledger-view').textContent), c => c.charCodeAt(0));
  const view = JSON.parse(new TextDecoder().decode(bytes));
  validateView(view);
  createRoot(document.getElementById('app')).render(<App view={view} />);
} catch {
  showFallback();
}
