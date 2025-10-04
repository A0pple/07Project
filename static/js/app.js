const redlink = {
  data: [],
  filter: '',
};

const shortFinder = {
  data: [],
  filter: '',
};

document.addEventListener('DOMContentLoaded', () => {
  setupRedlinkControls();
  setupShortControls();
  fetchRedlinkStatus();
  fetchShortStatus();
  setInterval(fetchRedlinkStatus, 4000);
  setInterval(fetchShortStatus, 4500);
});

function setupRedlinkControls() {
  const startBtn = document.getElementById('redlink-start');
  const stopBtn = document.getElementById('redlink-stop');
  const clearBtn = document.getElementById('redlink-clear');
  const exportBtn = document.getElementById('redlink-export');
  const filterInput = document.getElementById('redlink-filter');

  startBtn.addEventListener('click', () => {
    const payload = {
      rpm: parseFloat(document.getElementById('redlink-rpm').value) || 60,
      category: document.getElementById('redlink-category').value,
      links_per_page: parseInt(document.getElementById('redlink-links').value, 10) || 0,
    };
    postJSON('/api/redlinks/start', payload).then((resp) => {
      if (resp.error) {
        setStatusText('redlink-status', resp.error);
      }
      updateRedlinkUI(resp.status);
    });
  });

  stopBtn.addEventListener('click', () => {
    postJSON('/api/redlinks/stop').then((resp) => updateRedlinkUI(resp.status));
  });

  clearBtn.addEventListener('click', () => {
    postJSON('/api/redlinks/clear').then((resp) => updateRedlinkUI(resp.status));
  });

  exportBtn.addEventListener('click', () => {
    window.open('/api/redlinks/export', '_blank');
  });

  filterInput.addEventListener('input', (event) => {
    redlink.filter = event.target.value.toLowerCase();
    renderRedlinkRows();
  });
}

function setupShortControls() {
  const startBtn = document.getElementById('short-start');
  const stopBtn = document.getElementById('short-stop');
  const clearBtn = document.getElementById('short-clear');
  const exportBtn = document.getElementById('short-export');
  const filterInput = document.getElementById('short-filter');

  startBtn.addEventListener('click', () => {
    const payload = {
      rpm: parseFloat(document.getElementById('short-rpm').value) || 60,
      category: document.getElementById('short-category').value,
      max_bytes: parseInt(document.getElementById('short-max').value, 10) || 8000,
    };
    postJSON('/api/short/start', payload).then((resp) => {
      if (resp.error) {
        setStatusText('short-status', resp.error);
      }
      updateShortUI(resp.status);
    });
  });

  stopBtn.addEventListener('click', () => {
    postJSON('/api/short/stop').then((resp) => updateShortUI(resp.status));
  });

  clearBtn.addEventListener('click', () => {
    postJSON('/api/short/clear').then((resp) => updateShortUI(resp.status));
  });

  exportBtn.addEventListener('click', () => {
    window.open('/api/short/export', '_blank');
  });

  filterInput.addEventListener('input', (event) => {
    shortFinder.filter = event.target.value.toLowerCase();
    renderShortRows();
  });
}

function postJSON(url, payload) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload ? JSON.stringify(payload) : null,
  }).then((response) => response.json());
}

function fetchRedlinkStatus() {
  fetch('/api/redlinks/status')
    .then((resp) => resp.json())
    .then((data) => updateRedlinkUI(data))
    .catch(() => {});
}

function fetchShortStatus() {
  fetch('/api/short/status')
    .then((resp) => resp.json())
    .then((data) => updateShortUI(data))
    .catch(() => {});
}

function updateRedlinkUI(data) {
  if (!data) return;
  document.getElementById('redlink-start').disabled = !!data.running;
  document.getElementById('redlink-stop').disabled = !data.running;
  document.getElementById('redlink-status').textContent = data.status || 'Idle';
  document.getElementById('redlink-scanned').textContent = formatNumber(data.scanned);
  document.getElementById('redlink-count').textContent = formatNumber(data.red_links);
  document.getElementById('redlink-errors').textContent = formatNumber(data.errors);
  document.getElementById('redlink-elapsed').textContent = formatElapsed(data.elapsed);

  if (data.config) {
    document.getElementById('redlink-rpm').value = Math.round(data.config.rpm || DEFAULT_RPM);
    document.getElementById('redlink-category').value = data.config.category || '';
    document.getElementById('redlink-links').value = data.config.links_per_page ?? 25;
  }

  updateCategoryCounts(data.category_counts || {});
  renderDebug('redlink-debug', data.debug || []);
  redlink.data = data.results || [];
  renderRedlinkRows();
}

function updateShortUI(data) {
  if (!data) return;
  document.getElementById('short-start').disabled = !!data.running;
  document.getElementById('short-stop').disabled = !data.running;
  document.getElementById('short-status').textContent = data.status || 'Idle';
  document.getElementById('short-scanned').textContent = formatNumber(data.scanned);
  document.getElementById('short-count').textContent = formatNumber(data.matches);
  document.getElementById('short-errors').textContent = formatNumber(data.errors);
  document.getElementById('short-elapsed').textContent = formatElapsed(data.elapsed);

  if (data.config) {
    document.getElementById('short-rpm').value = Math.round(data.config.rpm || DEFAULT_RPM);
    document.getElementById('short-category').value = data.config.category || '';
    document.getElementById('short-max').value = data.config.max_bytes ?? 8000;
  }

  renderDebug('short-debug', data.debug || []);
  shortFinder.data = data.results || [];
  renderShortRows();
}

function renderRedlinkRows() {
  const tbody = document.querySelector('#redlink-table tbody');
  tbody.innerHTML = '';
  const filtered = redlink.data.filter((row) => {
    if (!redlink.filter) return true;
    const haystack = `${row.category} ${row.missing} ${row.source}`.toLowerCase();
    return haystack.includes(redlink.filter);
  });

  filtered.forEach((row) => {
    const tr = document.createElement('tr');
    const wiki = `https://en.wikipedia.org/wiki/${encodeURIComponent(row.missing)}`;
    const google = `https://www.google.com/search?q=${encodeURIComponent(row.missing)}`;
    const bing = `https://www.bing.com/search?q=${encodeURIComponent(row.missing)}`;
    tr.innerHTML = `
      <td>${escapeHtml(row.category || '')}</td>
      <td>
        <div class="primary">${escapeHtml(row.missing || '')}</div>
        <div class="links">
          <a href="${wiki}" target="_blank" rel="noopener">Wiki</a>
          <a href="${google}" target="_blank" rel="noopener">Google</a>
          <a href="${bing}" target="_blank" rel="noopener">Bing</a>
        </div>
      </td>
      <td>${escapeHtml(row.source || '')}</td>
      <td>${escapeHtml(row.sources || '?')}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderShortRows() {
  const tbody = document.querySelector('#short-table tbody');
  tbody.innerHTML = '';
  const filtered = shortFinder.data.filter((row) => {
    if (!shortFinder.filter) return true;
    const haystack = `${row.title} ${row.touched}`.toLowerCase();
    return haystack.includes(shortFinder.filter);
  });

  filtered.forEach((row) => {
    const tr = document.createElement('tr');
    const wiki = row.url || `https://en.wikipedia.org/wiki/${encodeURIComponent(row.title)}`;
    tr.innerHTML = `
      <td>
        <a href="${wiki}" target="_blank" rel="noopener">${escapeHtml(row.title || '')}</a>
      </td>
      <td>${escapeHtml(row.bytes || '?')}</td>
      <td>${escapeHtml(row.touched || '')}</td>
    `;
    tbody.appendChild(tr);
  });
}

function updateCategoryCounts(counts) {
  CATEGORY_ORDER.forEach((cat) => {
    const el = document.getElementById(`cat-${cat.replace(/\s+/g, '-').toLowerCase()}`);
    if (el) {
      el.textContent = formatNumber(counts[cat] || 0);
    }
  });
}

function renderDebug(elementId, rows) {
  const list = document.getElementById(elementId);
  if (!list) return;
  list.innerHTML = '';
  rows.forEach((entry) => {
    const li = document.createElement('li');
    li.textContent = `[${entry.time || '--:--:--'}] ${entry.message || ''}`;
    list.appendChild(li);
  });
}

function formatNumber(value) {
  if (value === undefined || value === null) return '0';
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return num.toLocaleString();
}

function formatElapsed(seconds) {
  if (!seconds || seconds <= 0) return '';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `Elapsed: ${mins}m ${secs}s`;
}

function setStatusText(elementId, message) {
  const el = document.getElementById(elementId);
  if (el) {
    el.textContent = message;
  }
}

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
