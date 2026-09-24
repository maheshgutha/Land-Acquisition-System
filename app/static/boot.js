'use strict';
// Entry point: restore a session if there is one, otherwise show the login screen.
(async function boot() {
  if (loadSession()) {
    try { await api('/api/auth/me'); await startApp(); return; } catch (_) { /* token expired */ }
  }
  showLogin();
})();
