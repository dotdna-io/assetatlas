// SPDX-License-Identifier: AGPL-3.0-or-later
import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles/tokens.css';
import './styles/dark-mode.css';
import './styles/animations.css';
import './styles/global.css';
import App from './App';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
