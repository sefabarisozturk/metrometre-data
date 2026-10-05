// metrometre — minimal service worker
// Amaç: uygulamanın "yüklenebilir" (installable) olması + uygulama kabuğunun
// (index.html) çevrimdışıyken son halinin gösterilmesi.
// Harita karoları, fontlar ve CSV/GeoJSON verisi ÖNBELLEĞE ALINMAZ — her zaman
// canlı ağdan çekilir. Böylece ilerleme verisi hep güncel kalır.

const CACHE = 'metrometre-shell-v2';
const SHELL = ['/', '/index.html'];

// Kurulum: uygulama kabuğunu önbelleğe al ve hemen etkinleş.
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE)
      .then(cache => cache.addAll(SHELL))
      .catch(() => {}) // kabuk önbelleklenemezse sessiz geç
  );
});

// Etkinleşme: eski sürüm önbelleklerini temizle, kontrolü hemen devral.
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// Fetch: yalnızca sayfa gezinmelerini ele al (ağ öncelikli, çevrimdışında kabuğa düş).
// Diğer tüm istekler (harita, font, veri) doğrudan ağa gider — araya girilmez.
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;

  if (req.mode === 'navigate') {
    // Yalnızca uygulama sayfaları (/, /index.html, /hat/*) kabuk olarak saklanır.
    // /bulten/* gibi ayrı sayfalar araya girilmeden ağdan gelir; aksi halde
    // çevrimdışı kabuk bir bülten sayfasıyla değişirdi.
    const path = new URL(req.url).pathname;
    const isApp = path === '/' || path === '/index.html' || path === '/hat' || path.startsWith('/hat/');
    if (!isApp) return;
    event.respondWith(
      fetch(req)
        .then(res => {
          // Son çevrimiçi halini kabuk olarak sakla
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put('/index.html', copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match('/index.html')) // çevrimdışı: son kabuk
    );
  }
});
