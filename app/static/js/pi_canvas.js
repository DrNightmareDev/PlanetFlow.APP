/**
 * PI Surface Template canvas renderer
 * Style: dark circle + coloured ring + icon, white links — matches eve-webtools.com/Planetary/
 *
 * JSON format (DalShooth / EVE in-game export):
 *   P  – pins:   [{T: typeId, La: lat_rad (colatitude), Lo: lon_rad, ...}]
 *   L  – links:  [{S: src_pin_idx, D: dst_pin_idx, Lv: level}]
 *   R  – routes: [{P: [idx0, idx1, …, idxN], T: typeId, Q: qty}]
 */

// ── Building family → display config ──────────────────────────────────────────
// icon: Unicode char rendered as white text on dark circle
// ring: coloured outer ring colour
// name: canonical display name

const PI_FAMILIES = {
  command_center:  { icon: '✦', ring: '#c8a600', name: 'Command Center' },
  launchpad:       { icon: '⬆', ring: '#00b4d8', name: 'Launch Pad' },
  storage:         { icon: '▣', ring: '#4cc9f0', name: 'Storage Facility' },
  ecu:             { icon: '⚙', ring: '#f4a300', name: 'Extractor Control Unit' },
  extractor_head:  { icon: '⊙', ring: '#57cc99', name: 'Extractor Head' },
  adv_industrial:  { icon: '⚙', ring: '#9b5de5', name: 'Adv. Industrial Facility' },
  basic_industrial:{ icon: '⚙', ring: '#3a86ff', name: 'Basic Industrial Facility' },
  high_tech:       { icon: '◈', ring: '#ff006e', name: 'High-Tech Production Plant' },
  unknown:         { icon: '?',  ring: '#666',    name: 'Unknown' },
};

// ── Type ID → family mapping (all planet-type variants from ESI) ───────────────
const PI_TYPE_FAMILY = {
  // Command Centers (base per planet type)
  2254: 'command_center', 2524: 'command_center', 2525: 'command_center',
  2533: 'command_center', 2534: 'command_center', 2549: 'command_center',
  2550: 'command_center', 2551: 'command_center',
  // Command Center upgrade tiers (Limited/Standard/Improved/Advanced/Elite)
  2129:'command_center',2130:'command_center',2131:'command_center',2132:'command_center',2133:'command_center',
  2134:'command_center',2135:'command_center',2136:'command_center',2137:'command_center',2138:'command_center',
  2139:'command_center',2140:'command_center',2141:'command_center',2142:'command_center',2143:'command_center',
  2144:'command_center',2145:'command_center',2146:'command_center',2147:'command_center',2148:'command_center',
  2149:'command_center',2150:'command_center',2151:'command_center',2152:'command_center',2153:'command_center',
  2154:'command_center',2155:'command_center',2156:'command_center',2157:'command_center',2158:'command_center',
  2159:'command_center',2160:'command_center',2574:'command_center',2576:'command_center',2577:'command_center',
  2578:'command_center',2581:'command_center',2582:'command_center',2585:'command_center',2586:'command_center',

  // Launch Pads (one per planet type)
  2256: 'launchpad', 2542: 'launchpad', 2543: 'launchpad', 2544: 'launchpad',
  2552: 'launchpad', 2555: 'launchpad', 2556: 'launchpad', 2557: 'launchpad',

  // Storage Facilities
  2257: 'storage', 2535: 'storage', 2536: 'storage', 2541: 'storage',
  2558: 'storage', 2560: 'storage', 2561: 'storage', 2562: 'storage',

  // Extractor Control Units (ECU)
  2848: 'ecu', 3060: 'ecu', 3061: 'ecu', 3062: 'ecu',
  3063: 'ecu', 3064: 'ecu', 3067: 'ecu', 3068: 'ecu',

  // Extractor Heads (template-internal IDs — may reuse building IDs for the
  // planet type; 2481 behaves as extractor head in miner templates)
  2481: 'extractor_head',

  // Advanced Industrial Facilities
  2470: 'adv_industrial', 2472: 'adv_industrial', 2474: 'adv_industrial',
  2480: 'adv_industrial', 2484: 'adv_industrial', 2485: 'adv_industrial',
  2491: 'adv_industrial', 2494: 'adv_industrial',

  // Basic Industrial Facilities
  2469: 'basic_industrial', 2471: 'basic_industrial', 2473: 'basic_industrial',
  2483: 'basic_industrial', 2490: 'basic_industrial', 2492: 'basic_industrial',
  2493: 'basic_industrial',

  // High-Tech Production Plants
  2475: 'high_tech', 2482: 'high_tech',
};

// Convenience lookups used by the legend
const PI_COLORS = Object.fromEntries(
  Object.entries(PI_TYPE_FAMILY).map(([tid, fam]) => [tid, PI_FAMILIES[fam].ring])
);
const PI_NAMES = Object.fromEntries(
  Object.entries(PI_TYPE_FAMILY).map(([tid, fam]) => [tid, PI_FAMILIES[fam].name])
);

function _getFamily(typeId) {
  return PI_TYPE_FAMILY[typeId] || 'unknown';
}
function _getFamilyConfig(typeId) {
  return PI_FAMILIES[_getFamily(typeId)];
}

// ── Pin drawer ─────────────────────────────────────────────────────────────────
// Style: dark filled circle + coloured outer ring + white icon text

const _BASE_PIN_R = 5; // base radius in px at scale=1

function drawPIPin(ctx, x, y, baseR, typeId, _unused) {
  const cfg = _getFamilyConfig(typeId);
  const outerR = baseR * 1.45;
  const innerR = baseR;

  // Outer coloured ring
  ctx.beginPath();
  ctx.arc(x, y, outerR, 0, Math.PI * 2);
  ctx.strokeStyle = cfg.ring;
  ctx.lineWidth = Math.max(1.5, outerR * 0.28);
  ctx.stroke();

  // Dark filled inner circle
  ctx.beginPath();
  ctx.arc(x, y, innerR, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(8,10,14,0.88)';
  ctx.fill();

  // Icon text (white, centred)
  const fontSize = Math.max(7, innerR * 1.1);
  ctx.font = `${fontSize}px sans-serif`;
  ctx.fillStyle = '#ffffff';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(cfg.icon, x, y);
  ctx.textBaseline = 'alphabetic'; // reset
}

// ── Link extraction (from L array: {S: src_idx, D: dst_idx}) ──────────────────
// IMPORTANT: S and D are 1-based pin indices — subtract 1 to get array index.
function extractLinks(layoutData) {
  const links = new Set();
  for (const lnk of (layoutData.L || [])) {
    if (lnk.S !== undefined && lnk.D !== undefined) {
      const s = lnk.S - 1, d = lnk.D - 1;
      links.add(Math.min(s, d) + '_' + Math.max(s, d));
    }
  }
  return links;
}

// ── Azimuthal equidistant projection ──────────────────────────────────────────
// La = colatitude (0=north pole, π/2=equator, π=south pole), Lo = longitude
// Same spherical convention as Three.js setFromSphericalCoords used by calli-eve/planetflow

function buildProjection(pins, W, H, pad, extraTransform) {
  if (!pins.length) return null;
  extraTransform = extraTransform || { x: 0, y: 0, scale: 1 };

  // Cluster centroid via 3-D Cartesian mean (handles longitude wrap-around)
  let cx = 0, cy = 0, cz = 0;
  for (const p of pins) {
    cx += Math.sin(p.La) * Math.cos(p.Lo);
    cy += Math.cos(p.La);
    cz += Math.sin(p.La) * Math.sin(p.Lo);
  }
  cx /= pins.length; cy /= pins.length; cz /= pins.length;
  const La0 = Math.atan2(Math.sqrt(cx*cx + cz*cz), cy);
  const Lo0 = Math.atan2(cz, cx);

  function project(La, Lo) {
    const cosC = Math.cos(La0)*Math.cos(La)
               + Math.sin(La0)*Math.sin(La)*Math.cos(Lo - Lo0);
    const c = Math.acos(Math.max(-1, Math.min(1, cosC)));
    if (c < 1e-10) return [0, 0];
    const k = c / Math.sin(c);
    return [
       k * Math.sin(La) * Math.sin(Lo - Lo0),
      -k * (Math.sin(La0)*Math.cos(La) - Math.cos(La0)*Math.sin(La)*Math.cos(Lo - Lo0)),
    ];
  }

  const pts = pins.map(p => project(p.La, p.Lo));
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const dX = maxX - minX || 1e-4;
  const dY = maxY - minY || 1e-4;

  const usableW = W - pad * 2, usableH = H - pad * 2;
  const baseScale = Math.min(usableW / dX, usableH / dY);
  const baseOffX  = pad + (usableW - dX * baseScale) / 2;
  const baseOffY  = pad + (usableH - dY * baseScale) / 2;

  const fn = function(La, Lo) {
    const [px, py] = project(La, Lo);
    return [
      (baseOffX + (px - minX) * baseScale) * extraTransform.scale + extraTransform.x,
      (baseOffY + (py - minY) * baseScale) * extraTransform.scale + extraTransform.y,
    ];
  };
  fn.baseScale = baseScale;
  return fn;
}

// ── Planet radius (metres, approximate per-type average) ──────────────────────
const PI_PLANET_RADIUS_M = {
  barren:     5_500_000,
  gas:       57_000_000,
  ice:       12_000_000,
  lava:       6_500_000,
  oceanic:   13_000_000,
  plasma:     7_500_000,
  storm:     35_000_000,
  temperate: 14_000_000,
};

// ── Structure footprint radius (metres, from EVE SDE radius attribute) ─────────
const PI_STRUCT_RADIUS_M = {
  command_center:   600,
  launchpad:        500,
  storage:          450,
  ecu:              400,
  extractor_head:   110,
  adv_industrial:   280,
  basic_industrial: 280,
  high_tech:        280,
  unknown:          250,
};

// ── Planet-type background gradient ───────────────────────────────────────────
const PI_PLANET_BG = {
  barren:    ['#1a0e00', '#2a1800', '#0a0500'],
  gas:       ['#0a1400', '#0e1c00', '#050a00'],
  oceanic:   ['#00080f', '#001018', '#00040a'],
  temperate: ['#050f05', '#081408', '#020802'],
  ice:       ['#060a10', '#080e18', '#04060c'],
  storm:     ['#0d0516', '#150820', '#08030d'],
  plasma:    ['#150500', '#1e0800', '#0a0300'],
  lava:      ['#1c0300', '#280400', '#0e0200'],
};

function drawPlanetBackground(ctx, W, H, planetType) {
  const pt = (planetType || '').toLowerCase();
  const [mid, edge, deep] = PI_PLANET_BG[pt] || ['#0a0c10', '#101418', '#060708'];
  const grad = ctx.createRadialGradient(W*0.45, H*0.4, 0, W*0.5, H*0.5, W*0.75);
  grad.addColorStop(0, mid);
  grad.addColorStop(0.55, edge);
  grad.addColorStop(1, deep);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, W, H);

  // Subtle noise overlay (random "surface texture" seeded by canvas size)
  const imageData = ctx.getImageData(0, 0, W, H);
  const d = imageData.data;
  // Cheap deterministic noise using sin
  for (let i = 0; i < d.length; i += 4) {
    const px = (i / 4) % W, py = Math.floor((i / 4) / W);
    const n = (Math.sin(px * 0.31 + py * 0.17) * 0.5 +
               Math.sin(px * 0.07 - py * 0.41) * 0.5) * 12 | 0;
    d[i]   = Math.min(255, Math.max(0, d[i]   + n));
    d[i+1] = Math.min(255, Math.max(0, d[i+1] + n));
    d[i+2] = Math.min(255, Math.max(0, d[i+2] + n));
  }
  ctx.putImageData(imageData, 0, 0);
}

// ── Full render ────────────────────────────────────────────────────────────────
/**
 * @param {HTMLCanvasElement} canvas
 * @param {object|string} layoutData
 * @param {object} opts
 *   pad          – canvas padding px  (default 12)
 *   pinScale     – base pin radius = pinScale * 5  (default 1)
 *   lineWidth    – link line width  (default 1)
 *   linkAlpha    – link opacity  (default 0.55)
 *   showLabels   – draw abbreviated labels when zoomed  (default false)
 *   transform    – {x, y, scale} for pan/zoom
 *   planetType   – string for background colour ("barren", "gas", …)
 *   showBg       – draw planet background  (default false)
 */
function renderPITemplate(canvas, layoutData, opts) {
  opts = opts || {};
  if (typeof layoutData === 'string') {
    try { layoutData = JSON.parse(layoutData); } catch(e) { return; }
  }
  if (!layoutData) return;

  const pins = layoutData.P || [];
  if (!pins.length) return;

  const W = canvas.width, H = canvas.height;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const pad       = opts.pad !== undefined ? opts.pad : 12;
  const transform = opts.transform || { x: 0, y: 0, scale: 1 };
  const toXY      = buildProjection(pins, W, H, pad, transform);
  if (!toXY) return;

  const baseR = (opts.pinScale || 1) * _BASE_PIN_R;
  const scaleMode = !!(opts.scaleMode && opts.planetRadiusM);

  // Background
  if (opts.showBg) {
    drawPlanetBackground(ctx, W, H, opts.planetType || '');
  } else {
    ctx.fillStyle = 'rgba(8,10,14,0.0)'; // transparent — CSS handles it
  }

  // ── Links ─────────────────────────────────────────────────────────────────
  const links = extractLinks(layoutData);
  const alpha = opts.linkAlpha !== undefined ? opts.linkAlpha : 0.55;
  ctx.strokeStyle = `rgba(255,255,255,${alpha})`;
  ctx.lineWidth = opts.lineWidth || 1;
  ctx.lineCap = 'round';
  for (const pair of links) {
    const [ai, bi] = pair.split('_').map(Number);
    if (ai >= pins.length || bi >= pins.length) continue;
    const [ax, ay] = toXY(pins[ai].La, pins[ai].Lo);
    const [bx, by] = toXY(pins[bi].La, pins[bi].Lo);
    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
  }

  // ── Pins ──────────────────────────────────────────────────────────────────
  for (const pin of pins) {
    const [x, y] = toXY(pin.La, pin.Lo);
    let r;
    if (scaleMode) {
      const fam = _getFamily(pin.T);
      const structM = PI_STRUCT_RADIUS_M[fam] || PI_STRUCT_RADIUS_M.unknown;
      r = Math.max(2, (structM / opts.planetRadiusM) * toXY.baseScale * transform.scale);
    } else {
      r = baseR * transform.scale;
    }
    drawPIPin(ctx, x, y, r, pin.T);
  }

  // ── Labels (zoomed detail view) ───────────────────────────────────────────
  if (opts.showLabels && transform.scale > 1.6) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const pin of pins) {
      const [x, y] = toXY(pin.La, pin.Lo);
      const cfg = _getFamilyConfig(pin.T);
      let _lr;
      if (scaleMode) {
        const fam = _getFamily(pin.T);
        const structM = PI_STRUCT_RADIUS_M[fam] || PI_STRUCT_RADIUS_M.unknown;
        _lr = Math.max(2, (structM / opts.planetRadiusM) * toXY.baseScale * transform.scale);
      } else {
        _lr = baseR * transform.scale;
      }
      const r = _lr * 1.45;
      const fontSize = Math.max(8, Math.min(11, r * 0.55));
      ctx.font = `${fontSize}px sans-serif`;
      ctx.fillStyle = cfg.ring + 'dd';
      ctx.fillText(cfg.name.split(' ')[0], x, y + r + 3);
    }
    ctx.textBaseline = 'alphabetic';
  }
}
