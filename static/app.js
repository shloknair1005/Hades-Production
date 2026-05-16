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