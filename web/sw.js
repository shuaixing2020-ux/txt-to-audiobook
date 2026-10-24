/* 有声书工坊 · Service Worker
   策略：外壳走「网络优先，失败回落缓存」——
     · 改了代码刷新立刻生效（不会被旧缓存粘住）
     · 断网时仍能打开界面
   接口与音频一律不缓存：数据要新，音频太大。 */
const CACHE = 'audiobook-shell-v1';
const SHELL = ['/', '/app.css', '/app.js', '/manifest.json',
               '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;      // 接口不缓存

  e.respondWith(
    fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }).catch(() => caches.match(req).then((hit) => {
      if (hit) return hit;
      return req.mode === 'navigate' ? caches.match('/') : Response.error();
    }))
  );
});
