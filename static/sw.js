const CACHE_NAME = 'decongestilaguna-v4';
const DATA_CACHE = 'decongestilaguna-data-v1';

// Pages & static assets to precache for offline access
const PRECACHE_URLS = [
  '/',
  '/dashboard',
  '/violations',
  '/calendar',
  '/settings',
  '/login',
  '/static/manifest.json',
  '/static/js/zoom.js',
  '/static/icons/icon-192.svg',
  '/static/icons/icon-512.svg',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/chart.js'
];

// API endpoints to cache for offline data access
const CACHEABLE_API = [
  '/api/violation_counts',
  '/api/violation_stats',
  '/api/violations_list',
  '/api/alert_config',
  '/api/alert_log',
  '/api/db_violations_count',
  '/api/events'
];

// ========== INSTALL: Precache all shell assets ==========
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        // Cache each URL individually — don't fail all if one fails
        return Promise.allSettled(
          PRECACHE_URLS.map(url =>
            cache.add(url).catch(err => console.warn('Precache skip:', url, err.message))
          )
        );
      })
      .then(() => self.skipWaiting())
  );
});

// ========== ACTIVATE: Clean old caches ==========
self.addEventListener('activate', event => {
  const keep = [CACHE_NAME, DATA_CACHE];
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => !keep.includes(k)).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ========== FETCH: Smart caching strategies ==========
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  if (!event.request.url.startsWith('http')) return;

  const url = new URL(event.request.url);

  // --- Strategy 1: API calls → Network-first, cache fallback for offline ---
  if (url.pathname.startsWith('/api/')) {
    // Only cache GET API calls that are in our cacheable list
    const isCacheable = CACHEABLE_API.some(api => url.pathname.startsWith(api));

    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok && isCacheable) {
            const clone = resp.clone();
            caches.open(DATA_CACHE).then(cache => cache.put(event.request, clone)).catch(() => {});
          }
          return resp;
        })
        .catch(() => {
          // Offline — serve from data cache
          return caches.match(event.request).then(cached => {
            if (cached) return cached;
            return new Response(JSON.stringify({ _offline: true, error: 'Offline' }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' }
            });
          });
        })
    );
    return;
  }

  // --- Strategy 2: HTML pages → Network-first, cache fallback ---
  if (event.request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone)).catch(() => {});
          }
          return resp;
        })
        .catch(() => {
          return caches.match(event.request).then(cached => {
            if (cached) return cached;
            // Fallback to index if specific page not cached
            return caches.match('/').then(index => index || new Response(offlineHTML(), {
              status: 200, headers: { 'Content-Type': 'text/html' }
            }));
          });
        })
    );
    return;
  }

  // --- Strategy 3: Static assets → Cache-first, network fallback ---
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(resp => {
        if (resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone)).catch(() => {});
        }
        return resp;
      }).catch(() => new Response('', { status: 408 }));
    })
  );
});

// ========== BACKGROUND SYNC: Push queued actions when online ==========
self.addEventListener('sync', event => {
  if (event.tag === 'sync-enforced') {
    event.waitUntil(syncEnforced());
  }
  if (event.tag === 'sync-offline-queue') {
    event.waitUntil(syncOfflineQueue());
  }
});

async function syncEnforced() {
  // Read pending enforced actions from the message channel
  const clients = await self.clients.matchAll();
  for (const client of clients) {
    client.postMessage({ type: 'SYNC_ENFORCED' });
  }
}

async function syncOfflineQueue() {
  const clients = await self.clients.matchAll();
  for (const client of clients) {
    client.postMessage({ type: 'SYNC_OFFLINE_QUEUE' });
  }
}

// ========== MESSAGE HANDLER ==========
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data === 'CLEAR_DATA_CACHE') {
    caches.delete(DATA_CACHE);
  }
});

// ========== OFFLINE FALLBACK HTML ==========
function offlineHTML() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Offline — DECONGESTILAGUNA</title>
  <style>
    body{background:#181c23;color:#f8f9fa;font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center;padding:1rem}
    .offline-box{max-width:360px}
    h1{color:#ff9800;font-size:1.5rem}
    p{color:#bfc7d5;font-size:0.95rem}
    button{background:#ff9800;color:#000;border:none;padding:0.6rem 1.5rem;border-radius:8px;font-weight:600;cursor:pointer;margin-top:1rem;font-size:0.95rem}
    button:hover{background:#e68a00}
  </style>
</head>
<body>
  <div class="offline-box">
    <h1>You're Offline</h1>
    <p>DECONGESTILAGUNA is currently unavailable. Your cached pages and data are still accessible from the navigation.</p>
    <button onclick="location.reload()">Try Again</button>
  </div>
</body>
</html>`;
}
