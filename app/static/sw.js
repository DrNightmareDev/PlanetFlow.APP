// EVE PI Manager – Service Worker
// Sendet Browser-Benachrichtigungen wenn Extractor-Ablaufzeiten näher rücken.

const NOTIFY_THRESHOLDS = [120, 60, 30, 10]; // Minuten vor Ablauf
const CHECK_INTERVAL_MS = 60 * 1000;          // jede Minute prüfen

let expiries = [];   // [{name, char, expiry_ts}]
let notified  = new Set();
let checkTimer = null;

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));

// Empfange Ablauf-Daten von der Dashboard-Seite
self.addEventListener('message', event => {
    if (event.data?.type === 'SET_EXPIRIES') {
        expiries = event.data.expiries || [];
        notified = new Set();
        clearTimeout(checkTimer);
        if (expiries.length > 0) scheduleCheck();
    }
});

// Klick auf Notification → Dashboard öffnen
self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
            for (const c of list) {
                if (c.url.includes('/dashboard')) { c.focus(); return; }
            }
            return clients.openWindow('/dashboard');
        })
    );
});

function scheduleCheck() {
    checkTimer = setTimeout(() => {
        checkExpiries();
        if (expiries.some(e => e.expiry_ts > Date.now())) scheduleCheck();
    }, CHECK_INTERVAL_MS);
}

function checkExpiries() {
    const now = Date.now();
    for (const exp of expiries) {
        const minLeft = (exp.expiry_ts - now) / 60000;
        if (minLeft <= 0) continue;

        for (const threshold of NOTIFY_THRESHOLDS) {
            if (minLeft <= threshold) {
                const key = `${exp.name}:${threshold}`;
                if (!notified.has(key)) {
                    notified.add(key);
                    const label = threshold >= 60
                        ? `${threshold / 60}h`
                        : `${threshold} Min.`;
                    self.registration.showNotification('EVE PI Manager – Extractor läuft ab', {
                        body: `${exp.name} (${exp.char}) läuft in ${label} ab!`,
                        icon: '/static/img/favicon.svg',
                        badge: '/static/img/favicon.svg',
                        tag: key,
                        requireInteraction: threshold <= 30,
                        data: {url: '/dashboard'},
                    });
                }
            }
        }
    }
}
