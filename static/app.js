/* ── Config ─────────────────────────────────────────────────────────────────── */
const API = '';  // same origin — FastAPI serves both

/* ── Auth helpers ───────────────────────────────────────────────────────────── */
const Auth = {
  get token() { return localStorage.getItem('access_token'); },
  get refresh() { return localStorage.getItem('refresh_token'); },
  get user() { try { return JSON.parse(localStorage.getItem('user') || 'null'); } catch { return null; } },

  save(data) {
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
  },
  saveUser(u) { localStorage.setItem('user', JSON.stringify(u)); },

  clear() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
  },

  isLoggedIn() { return !!this.token; },

  requireLogin() {
    if (!this.isLoggedIn()) { window.location.href = '/static/login.html'; return false; }
    return true;
  },

  async refreshToken() {
    const rt = this.refresh;
    if (!rt) return false;
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) { this.clear(); return false; }
      const data = await res.json();
      this.save(data);
      return true;
    } catch { return false; }
  },
};

/* ── API client ─────────────────────────────────────────────────────────────── */
const api = {
  async request(method, path, body, retry = true) {
    const headers = { 'Content-Type': 'application/json' };
    if (Auth.token) headers['Authorization'] = `Bearer ${Auth.token}`;

    const res = await fetch(`${API}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    // Auto-refresh on 401
    if (res.status === 401 && retry) {
      const ok = await Auth.refreshToken();
      if (ok) return this.request(method, path, body, false);
      Auth.clear();
      window.location.href = '/static/login.html';
      return;
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw { status: res.status, ...err };
    }

    if (res.status === 204) return null;
    return res.json();
  },

  get(path) { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },
  patch(path, body) { return this.request('PATCH', path, body); },
  del(path) { return this.request('DELETE', path); },
};

/* ── Theme ──────────────────────────────────────────────────────────────────── */
const Theme = {
  get current() { return localStorage.getItem('theme') || 'dark'; },
  apply(t) {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('theme', t);
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
  },
  toggle() { this.apply(this.current === 'dark' ? 'light' : 'dark'); },
  init() { this.apply(this.current); },
};

/* ── Nav ────────────────────────────────────────────────────────────────────── */
function renderNav(active) {
  const user = Auth.user;
  const isSuperAdmin = user?.role === 'super_admin';
  const isAdmin = user?.role === 'admin' || user?.is_power_user || isSuperAdmin;

  const links = [
    { href: '/static/index.html', label: '⚖ Council', key: 'council' },
    { href: '/static/dashboard.html', label: '⌂ Dashboard', key: 'dashboard' },
    { href: '/static/analytics.html', label: '◈ Analytics', key: 'analytics' },
  ];
  if (isAdmin) links.push({ href: '/static/admin.html', label: '⚙ Admin', key: 'admin' });
  if (isSuperAdmin) links.push({ href: '/static/superadmin.html', label: '𓂀 Monitor', key: 'superadmin' });

  document.getElementById('nav').innerHTML = `
    <a class="nav-brand" href="/static/index.html">
      <span class="crown">𓂀</span>
      Judges of Hades
    </a>
    <nav class="nav-links">
      ${links.map(l => `
        <a class="nav-link ${active === l.key ? 'active' : ''}" href="${l.href}">${l.label}</a>
      `).join('')}
    </nav>
    <div class="nav-right">
      ${user ? `<span class="nav-user">${user.display_name}</span>` : ''}
      ${isSuperAdmin ? `<span class="badge badge-failed" style="font-size:0.7rem">SUPER ADMIN</span>` : ''}
      <button class="theme-toggle" id="theme-toggle" onclick="Theme.toggle()" title="Toggle theme"></button>
      ${user ? `<button class="btn btn-ghost btn-sm" onclick="logout()">Sign out</button>` : ''}
    </div>
  `;
  Theme.init();
}

async function logout() {
  try {
    await api.post('/auth/logout', { refresh_token: Auth.refresh });
  } catch { }
  Auth.clear();
  window.location.href = '/static/login.html';
}

/* ── Utilities ──────────────────────────────────────────────────────────────── */
function timeAgo(iso) {
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString();
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
  });
}

function statusBadge(s) {
  return `<span class="badge badge-${s}">${s}</span>`;
}

function visibilityBadge(v) {
  return `<span class="badge badge-${v}">${v}</span>`;
}

function showAlert(containerId, msg, type = 'error') {
  const el = document.getElementById(containerId);
  if (el) el.innerHTML = `<div class="alert alert-${type}">${msg}</div>`;
}

function clearAlert(containerId) {
  const el = document.getElementById(containerId);
  if (el) el.innerHTML = '';
}

function setLoading(btnId, loading, label = 'Submit') {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  btn.innerHTML = loading
    ? `<span class="spinner"></span> Working...`
    : label;
}

function accordion(el) {
  el.classList.toggle('open');
}
/* ── RunWatcher — global background poller ───────────────────────────────────
   Lives in app.js so it runs on EVERY page.
   Polls the active run every 3s regardless of which page the user is on.
   Other pages (index.html) register callbacks to react to state changes.
   State is persisted in localStorage so page navigation doesn't lose it.
────────────────────────────────────────────────────────────────────────────── */
const RunWatcher = {
  _KEY: 'hades_active_run',
  _timer: null,
  _callbacks: [],          // registered by index.html when it's open
  _INTERVAL: 3000,
  _MAX_AGE_MS: 10 * 60 * 1000,   // forget runs older than 10 min

  /* Save a new run as active */
  start(run, problemId, problemText) {
    localStorage.setItem(this._KEY, JSON.stringify({
      runId: run.id,
      version: run.version,
      problemId,
      problemText,
      status: run.status,
      savedAt: Date.now(),
    }));
    this._poll();
    if (!this._timer) this._timer = setInterval(() => this._poll(), this._INTERVAL);
    this._updateNavBadge('running');
  },

  /* Read persisted state — returns null if none / expired */
  get() {
    try {
      const raw = localStorage.getItem(this._KEY);
      if (!raw) return null;
      const s = JSON.parse(raw);
      if (Date.now() - s.savedAt > this._MAX_AGE_MS) { this.clear(); return null; }
      return s;
    } catch { return null; }
  },

  /* Register a callback (index.html registers on load) */
  onChange(fn) { this._callbacks.push(fn); },

  /* Remove all callbacks (called when index.html unloads — not strictly needed) */
  clearCallbacks() { this._callbacks = []; },

  /* Stop watching and remove state */
  clear() {
    clearInterval(this._timer);
    this._timer = null;
    localStorage.removeItem(this._KEY);
    this._callbacks = [];
    this._updateNavBadge(null);
  },

  /* Resume watching a run that was already in progress (called by index.html on load) */
  resume() {
    const s = this.get();
    if (!s) return null;
    if (s.status === 'done' || s.status === 'failed') return s;
    // Re-attach poll if not already running
    if (!this._timer) this._timer = setInterval(() => this._poll(), this._INTERVAL);
    this._updateNavBadge('running');
    return s;
  },

  /* Internal poll — fires every 3s on any page */
  async _poll() {
    const s = this.get();
    if (!s) { clearInterval(this._timer); this._timer = null; return; }
    if (s.status === 'done' || s.status === 'failed') {
      clearInterval(this._timer); this._timer = null;
      this._updateNavBadge(null);
      return;
    }
    try {
      const run = await api.get(`/runs/${s.runId}`);
      // Update persisted status
      const updated = { ...s, status: run.status };
      localStorage.setItem(this._KEY, JSON.stringify(updated));

      if (run.status === 'done' || run.status === 'failed') {
        clearInterval(this._timer);
        this._timer = null;
        this._updateNavBadge(run.status === 'done' ? 'done' : 'failed');
        // Notify any registered callbacks (index.html if it's open)
        this._callbacks.forEach(fn => fn(run, s.problemId));
        // If user is NOT on council page, update nav to signal completion
        if (!window.location.pathname.endsWith('index.html') &&
          window.location.pathname !== '/') {
          this._flashNav(run.status);
        }
      } else {
        this._callbacks.forEach(fn => fn(run, s.problemId));
      }
    } catch { }
  },

  /* Show a pulsing dot next to the Council nav link while running */
  _updateNavBadge(status) {
    const link = document.querySelector('.nav-link[href="/static/index.html"]');
    if (!link) return;
    // Remove existing badge
    link.querySelectorAll('.run-nav-badge').forEach(el => el.remove());
    if (!status) return;
    const dot = document.createElement('span');
    dot.className = 'run-nav-badge';
    dot.style.cssText = `
      display:inline-block;width:7px;height:7px;border-radius:50%;
      margin-left:5px;vertical-align:middle;flex-shrink:0;
      background:${status === 'done' ? '#58D68D' : status === 'failed' ? '#EC7063' : '#C9A84C'};
      ${status === 'running' ? 'animation:pulse 1.5s ease-in-out infinite;' : ''}
    `;
    link.appendChild(dot);
  },

  /* Briefly highlight the nav link when deliberation completes on another page */
  _flashNav(status) {
    const link = document.querySelector('.nav-link[href="/static/index.html"]');
    if (!link) return;
    const msg = status === 'done' ? '✓ Verdict ready' : '✗ Run failed';
    const orig = link.textContent.trim();
    link.style.color = status === 'done' ? '#58D68D' : '#EC7063';
    link.title = msg;
    setTimeout(() => {
      link.style.color = '';
      link.title = '';
    }, 8000);
  },
};

/* Auto-resume on every page load if there's an active run */
document.addEventListener('DOMContentLoaded', () => {
  const s = RunWatcher.get();
  if (s && s.status !== 'done' && s.status !== 'failed') {
    RunWatcher.resume();
  }
});