const STORAGE_KEY = 'vconApiKey';
const POLL_INTERVAL_MS = 3000;

let apiKey = null;
let pollTimer = null;
let renderedIds = new Set();

const authGate = document.getElementById('auth-gate');
const mainDiv = document.getElementById('main');
const authForm = document.getElementById('auth-form');
const apiKeyInput = document.getElementById('api-key-input');
const statusEl = document.getElementById('status');
const countBadge = document.getElementById('count-badge');
const vconList = document.getElementById('vcon-list');
const emptyState = document.getElementById('empty-state');
const resetBtn = document.getElementById('reset-btn');

function showAuthGate() {
  authGate.style.display = 'flex';
  mainDiv.style.display = 'none';
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function showMain() {
  authGate.style.display = 'none';
  mainDiv.style.display = 'block';
}

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || '';
}

function summarise(vcon) {
  const p = vcon.payload;
  if (!p || typeof p !== 'object') return 'vCon received';

  // IETF vCon top-level fields
  const parts = p.parties || p.participants || [];
  const tel = parts
    .map(p => p.tel || p.mailto || p.name || '')
    .filter(Boolean)
    .join(' ↔ ');

  const duration = p.duration != null
    ? `${Math.round(p.duration)}s`
    : (p.dialog && p.dialog[0] && p.dialog[0].duration != null
        ? `${Math.round(p.dialog[0].duration)}s`
        : null);

  const parts2 = [tel || 'Unknown parties', duration ? `${duration}` : null].filter(Boolean);
  return parts2.join(' · ') || 'vCon received';
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  } catch { return iso; }
}

function renderVcon(vcon, prepend = false) {
  if (renderedIds.has(vcon.id)) return;
  renderedIds.add(vcon.id);

  const card = document.createElement('div');
  card.className = 'vcon-card';
  card.dataset.id = vcon.id;

  const header = document.createElement('div');
  header.className = 'vcon-header';
  header.innerHTML = `
    <div class="vcon-meta">
      <span class="vcon-summary">${escapeHtml(summarise(vcon))}</span>
      <span class="vcon-time">${formatTime(vcon.received_at)}</span>
    </div>
    <span class="vcon-chevron">▶</span>
  `;

  const body = document.createElement('div');
  body.className = 'vcon-body';
  body.innerHTML = `<pre>${escapeHtml(JSON.stringify(vcon.payload, null, 2))}</pre>`;

  header.addEventListener('click', () => card.classList.toggle('expanded'));
  card.appendChild(header);
  card.appendChild(body);

  if (prepend) {
    vconList.insertBefore(card, vconList.firstChild);
  } else {
    vconList.appendChild(card);
  }
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function poll() {
  try {
    const res = await fetch('/api/vcons', {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (res.status === 401) {
      setStatus('API key rejected — reset and try again', 'error');
      return;
    }
    if (!res.ok) {
      setStatus(`Server error ${res.status}`, 'error');
      return;
    }
    const vcons = await res.json();
    setStatus('Live', 'live');
    countBadge.textContent = `${vcons.length} vCon${vcons.length !== 1 ? 's' : ''}`;

    // New items are prepended (store returns newest-first)
    for (const v of vcons) renderVcon(v, true);

    emptyState.style.display = vcons.length === 0 ? 'block' : 'none';
  } catch (err) {
    setStatus('Connection error', 'error');
  }
}

function startPolling() {
  showMain();
  renderedIds.clear();
  vconList.innerHTML = '';
  poll();
  pollTimer = setInterval(poll, POLL_INTERVAL_MS);
}

// Form submit — save key and start
authForm.addEventListener('submit', e => {
  e.preventDefault();
  const val = apiKeyInput.value.trim();
  if (!val) return;
  apiKey = val;
  localStorage.setItem(STORAGE_KEY, apiKey);
  startPolling();
});

// Reset
resetBtn.addEventListener('click', () => {
  localStorage.removeItem(STORAGE_KEY);
  apiKey = null;
  apiKeyInput.value = '';
  showAuthGate();
});

// On load — restore key from storage
const stored = localStorage.getItem(STORAGE_KEY);
if (stored) {
  apiKey = stored;
  startPolling();
} else {
  showAuthGate();
}
