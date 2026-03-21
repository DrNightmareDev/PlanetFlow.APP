/**
 * EVE PI Manager - App JavaScript
 */

'use strict';

// ============ ESI Status Check ============
(function checkESIStatus() {
    const dot = document.getElementById('esiStatusDot');
    const text = document.getElementById('esiStatusText');
    if (!dot || !text) return;

    fetch('https://esi.evetech.net/latest/status/?datasource=tranquility', {
        method: 'GET',
        headers: { 'Accept': 'application/json' }
    })
    .then(res => {
        if (!res.ok) throw new Error('ESI nicht erreichbar');
        return res.json();
    })
    .then(data => {
        const players = data.players || 0;
        const vip = data.vip || false;
        dot.classList.add('online');
        if (vip) {
            dot.classList.remove('online');
            dot.style.background = '#f4a300';
            text.textContent = `ESI: VIP Modus (${players.toLocaleString('de')} Spieler)`;
        } else {
            text.textContent = `ESI: Online · ${players.toLocaleString('de')} Spieler`;
        }
    })
    .catch(() => {
        dot.classList.add('offline');
        text.textContent = 'ESI: Nicht erreichbar';
    });
})();

// ============ Navbar Active State ============
(function setNavActive() {
    const path = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href && path.startsWith(href) && href !== '/') {
            link.classList.add('active');
        }
    });
})();

// ============ Auto-collapse navbar on mobile ============
document.addEventListener('DOMContentLoaded', function() {
    const navLinks = document.querySelectorAll('.navbar-nav .nav-link:not(.dropdown-toggle)');
    const navCollapse = document.getElementById('navMain');

    if (navCollapse) {
        navLinks.forEach(link => {
            link.addEventListener('click', () => {
                if (window.innerWidth < 992) {
                    const bsCollapse = bootstrap.Collapse.getInstance(navCollapse);
                    if (bsCollapse) bsCollapse.hide();
                }
            });
        });
    }
});

// ============ Number Formatting ============
function formatISK(value) {
    if (!value || value === 0) return '—';
    if (value >= 1e12) return (value / 1e12).toFixed(2) + ' T';
    if (value >= 1e9) return (value / 1e9).toFixed(2) + ' B';
    if (value >= 1e6) return (value / 1e6).toFixed(2) + ' M';
    if (value >= 1e3) return (value / 1e3).toFixed(0) + ' K';
    return value.toFixed(2);
}

// ============ Toast Notifications ============
function showToast(message, type = 'info') {
    const colors = {
        info: 'var(--eve-accent)',
        success: 'var(--eve-green)',
        warning: 'var(--eve-gold)',
        error: 'var(--eve-red)',
    };
    const toast = document.createElement('div');
    toast.style.cssText = `
        position: fixed; bottom: 20px; right: 20px; z-index: 9999;
        background: var(--eve-bg-2); border: 1px solid ${colors[type] || colors.info};
        color: var(--eve-text); padding: 0.75rem 1.25rem; border-radius: 6px;
        font-size: 0.875rem; box-shadow: 0 4px 16px rgba(0,0,0,0.4);
        animation: fadeIn 0.2s ease;
        max-width: 320px;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============ Confirm dialogs (standard via onclick) ============
// All confirm dialogs are handled via onclick="return confirm('...')" in HTML

// ============ Portrait Error Fallback ============
document.querySelectorAll('img[onerror]').forEach(img => {
    img.addEventListener('error', function() {
        if (!this.dataset.errored) {
            this.dataset.errored = '1';
            this.src = '/static/img/default_char.svg';
        }
    });
});

// ============ Colony Table: Sort + Filter ============
document.addEventListener('DOMContentLoaded', function () {
    const table = document.getElementById('coloniesTable');
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    const badge = document.getElementById('colonyCountBadge');
    let sortCol = null, sortAsc = true;

    // --- Sort ---
    table.querySelectorAll('th.eve-sortable').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (sortCol === col) {
                sortAsc = !sortAsc;
            } else {
                sortCol = col;
                sortAsc = true;
            }
            // Update icons
            table.querySelectorAll('th.eve-sortable').forEach(h => {
                h.classList.remove('sort-asc', 'sort-desc');
                h.querySelector('.eve-sort-icon').className = 'bi bi-chevron-expand eve-sort-icon';
            });
            th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
            const icon = th.querySelector('.eve-sort-icon');
            icon.className = sortAsc ? 'bi bi-chevron-up eve-sort-icon' : 'bi bi-chevron-down eve-sort-icon';

            sortRows(col, sortAsc);
        });
    });

    function sortRows(col, asc) {
        const attrMap = {
            char: 'sortChar', planet: 'sortPlanet', type: 'sortType',
            level: 'sortLevel', tier: 'sortTier', expiry: 'sortExpiry', isk: 'sortIsk'
        };
        const attr = attrMap[col];
        if (!attr) return;

        const sorted = [...rows].sort((a, b) => {
            let av = a.dataset[attr] || '';
            let bv = b.dataset[attr] || '';
            // Numeric columns
            if (col === 'expiry' || col === 'isk' || col === 'level') {
                av = parseFloat(av) || 0;
                bv = parseFloat(bv) || 0;
                return asc ? av - bv : bv - av;
            }
            // Tier: P0 < P1 < P2 < P3 < P4
            if (col === 'tier') {
                av = parseInt(av.replace('P', '')) || 0;
                bv = parseInt(bv.replace('P', '')) || 0;
                return asc ? av - bv : bv - av;
            }
            return asc ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        sorted.forEach(r => tbody.appendChild(r));
        applyFilter();
    }

    // --- Filter ---
    const filterSelect = document.getElementById('charFilter');
    if (filterSelect) {
        filterSelect.addEventListener('change', applyFilter);
    }

    function applyFilter() {
        const val = filterSelect ? filterSelect.value : '';
        let visible = 0;
        tbody.querySelectorAll('tr').forEach(r => {
            const show = !val || r.dataset.char === val;
            r.style.display = show ? '' : 'none';
            if (show) visible++;
        });
        if (badge) badge.textContent = visible;
    }
});
