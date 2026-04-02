// PlanetFlow – Service Worker
// Sendet Browser-Benachrichtigungen wenn Extractor-Ablaufzeiten näher rücken.

const CHECK_INTERVAL_MS = 60 * 1000; // jede Minute prüfen
const BATCH_WINDOW_MS   = 10 * 60 * 1000; // 10 Min Zusammenfassungs-Fenster

let expiries      = [];        // [{name, char, system, planet_type, expiry_ts}]
let notified      = new Set(); // "char:name:threshold" — bereits gefeuert
let lastCharNotify = new Map(); // char -> timestamp der letzten Notification
let threshold     = 60;        // Minuten vor Ablauf (konfigurierbar)
let checkTimer    = null;

self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));

// Empfange Daten von der Dashboard-Seite
self.addEventListener('message', event => {
    if (event.data?.type === 'SET_EXPIRIES') {
        expiries  = event.data.expiries  || [];
        threshold = event.data.threshold || 60;
        notified  = new Set();
        lastCharNotify = new Map();
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

    // Alle fälligen Einträge sammeln (innerhalb des Threshold-Fensters)
    // Gruppiert nach Charakter
    const pending = new Map(); // char -> [entry, ...]

    for (const exp of expiries) {
        const minLeft = (exp.expiry_ts - now) / 60000;
        if (minLeft <= 0 || minLeft > threshold) continue;

        const key = `${exp.char}:${exp.name}:${threshold}`;
        if (notified.has(key)) continue;

        if (!pending.has(exp.char)) pending.set(exp.char, []);
        pending.get(exp.char).push({ ...exp, key, minLeft });
    }

    for (const [char, entries] of pending) {
        const lastNotify = lastCharNotify.get(char) || 0;
        const sinceLastMs = now - lastNotify;

        if (sinceLastMs < BATCH_WINDOW_MS && lastNotify > 0) {
            // Innerhalb des Batch-Fensters → unterdrücken (wurden schon gemeldet)
            entries.forEach(e => notified.add(e.key));
            continue;
        }

        // Alle als notified markieren
        entries.forEach(e => notified.add(e.key));
        lastCharNotify.set(char, now);

        if (entries.length === 1) {
            // Einzelne Notification
            const e = entries[0];
            const timeLabel = formatMinutes(e.minLeft);
            self.registration.showNotification(`EVE PI – ${char}`, {
                body: `${e.name} (${e.system}) läuft in ${timeLabel} ab!`,
                icon:  '/static/img/favicon.svg',
                badge: '/static/img/favicon.svg',
                tag:   `planetflow-${char}`,
                requireInteraction: e.minLeft <= 30,
                data: { url: '/dashboard' },
            });
        } else {
            // Zusammenfassende Notification für mehrere Kolonien
            const lines = entries.map(e =>
                `• ${e.name} / ${e.system} (${formatMinutes(e.minLeft)})`
            ).join('\n');
            self.registration.showNotification(`EVE PI – ${char}: ${entries.length} Kolonien laufen ab`, {
                body: lines,
                icon:  '/static/img/favicon.svg',
                badge: '/static/img/favicon.svg',
                tag:   `planetflow-${char}`,
                requireInteraction: true,
                data: { url: '/dashboard' },
            });
        }
    }
}

function formatMinutes(min) {
    if (min >= 1440) return `${Math.round(min / 1440)}T`;
    if (min >= 60)   return `${Math.round(min / 60)}h`;
    return `${Math.round(min)} Min.`;
}
