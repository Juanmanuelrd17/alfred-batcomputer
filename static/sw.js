// 'Puter Batcomputer BCv1.2 Service Worker
const CACHE_NAME = 'puter-bcv1.2-v1';
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Network first with fallback
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
