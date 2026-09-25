// Page behaviour (entrance, reveal, header, nav glow, copy button) and the
// panel demo. The .js class gates every hidden state in CSS, so without this
// file the page is static and fully readable: the panel then shows its first
// frame as a plain image.
document.documentElement.classList.add('js');

// The panel controls only work with this file, so they are hidden in the
// markup and shown here, first, before anything that could fail.
document.getElementById('demo-controls')?.removeAttribute('hidden');

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------- header ----------

const header = $('#top-bar');
const syncHeader = () => header.classList.toggle('scrolled', window.scrollY > 8);
syncHeader();
window.addEventListener('scroll', syncHeader, { passive: true });

// ---------- panel ----------

// Every frame in screens/ is a six-entry palette PNG written by
// tools/build_screens.py with the integration's own renderer. Nothing here
// draws the dashboard; this only decides which frame is on the glass and how
// the glass shows it.

const W = 800;
const H = 480;
// In the order the year runs, so the green button always moves forward.
const SEASONS = ['spring', 'summer', 'autumn', 'winter'];

// The renderer's inks (dashboard_render.SPECTRA6), and what they look like on
// the real panel in daylight. The panel is reflective, so its white is a cool
// grey and its red reads as maroon; these sit between the community's
// calibrated Spectra 6 measurements and the flat values, so the page stays
// legible while still looking like paper rather than a screen.
const INKS = [
  { rgb: [0, 0, 0], paper: [35, 38, 42] },
  { rgb: [255, 255, 255], paper: [201, 209, 207] },
  { rgb: [200, 0, 0], paper: [152, 42, 37] },
  { rgb: [0, 150, 0], paper: [48, 96, 60] },
  { rgb: [0, 0, 200], paper: [44, 78, 160] },
  { rgb: [255, 230, 0], paper: [216, 204, 60] },
];
const [BLACK, WHITE] = INKS;
const key = ([r, g, b]) => (r << 16) | (g << 8) | b;
const inkIndex = new Map(INKS.map((ink, index) => [key(ink.rgb), index]));

// A fixed grain, so "On the wall" has the faint mottle of the real panel and
// the same frame always looks the same.
const grain = (() => {
  const noise = new Int8Array(W * H);
  let seed = 0x9e3779b9;
  for (let i = 0; i < noise.length; i++) {
    seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
    noise[i] = ((seed >>> 0) % 11) - 5;
  }
  return noise;
})();

const panel = $('#panel');
const paper = $('#paper');
const still = $('#panel-still');
const canvas = $('#panel-canvas');
const ctx = canvas.getContext('2d', { willReadFrequently: false });

// The panel opens on the visitor's own season (northern hemisphere, where the
// panel hangs). Without JS the plain image shows the summer frame instead.
const seasonNow = () => SEASONS[(Math.floor(((new Date().getMonth() + 10) % 12) / 3))];
const state = { season: seasonNow(), lang: 'en', page: 1 };
const frameName = (s = state) => `${s.season}-${s.lang}-p${s.page}`;

let scenes = {};
fetch('screens/scenes.json')
  .then((response) => (response.ok ? response.json() : []))
  .then((list) => { scenes = Object.fromEntries(list.map((scene) => [scene.key, scene])); describe(); })
  .catch(() => {});

// Decoded frames as ink indices, one byte per pixel: 384 KB each, and the
// looks are cheap to derive from them.
const frames = new Map();
const scratch = document.createElement('canvas');
scratch.width = W;
scratch.height = H;
const scratchCtx = scratch.getContext('2d', { willReadFrequently: true });

function loadFrame(name) {
  if (frames.has(name)) return frames.get(name);
  const promise = new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => {
      scratchCtx.drawImage(image, 0, 0);
      const rgba = scratchCtx.getImageData(0, 0, W, H).data;
      const indices = new Uint8Array(W * H);
      for (let i = 0, p = 0; i < indices.length; i++, p += 4) {
        // Anything off-palette (there should be none) falls back to white.
        indices[i] = inkIndex.get((rgba[p] << 16) | (rgba[p + 1] << 8) | rgba[p + 2]) ?? 1;
      }
      resolve(indices);
    };
    image.onerror = () => { frames.delete(name); reject(new Error(`frame ${name}`)); };
    image.src = `screens/${name}.png`;
  });
  frames.set(name, promise);
  return promise;
}

const colour = (ink) => ink.paper;

// Ink indices to pixels as the panel shows them, optionally as a negative.
function paint(indices, { invert = false } = {}) {
  const image = ctx.createImageData(W, H);
  const out = image.data;
  const palette = INKS.map((ink) => colour(ink));
  for (let i = 0, p = 0; i < indices.length; i++, p += 4) {
    let index = indices[i];
    if (invert) index = index === 0 ? 1 : index === 1 ? 0 : index;
    const source = palette[index];
    const n = grain[i];
    out[p] = source[0] + n;
    out[p + 1] = source[1] + n;
    out[p + 2] = source[2] + n;
    out[p + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
}

function fill(ink) {
  const [r, g, b] = colour(ink);
  ctx.fillStyle = `rgb(${r} ${g} ${b})`;
  ctx.fillRect(0, 0, W, H);
}

// Which pixel's pigment arrives first. Fixed, so a repaint is repeatable, and
// uncorrelated with the grain, so the arrival does not follow the texture.
const arrival = (() => {
  const order = new Uint8Array(W * H);
  let seed = 0x2545f491;
  for (let i = 0; i < order.length; i++) {
    seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
    order[i] = (seed >>> 0) & 255;
  }
  return order;
})();

const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

// When each ink develops, in ms after the old picture went negative. A
// Spectra 6 panel does not fade a picture in: it drives one pigment at a time,
// over the ghost of whatever was there before. White and black come first and
// draw the structure, every coloured area waits as a pale grey shadow of
// itself, then the colours arrive one after another, grain by grain, and
// yellow, the slowest pigment, lands last.
const LAYERS = [
  /* black  */ [80, 420],
  /* white  */ [0, 360],
  /* red    */ [540, 760],
  /* green  */ [420, 640],
  /* blue   */ [300, 520],
  /* yellow */ [660, 920],
];
const DEVELOP_MS = 1000;
const negative = (index) => (index === 0 ? 1 : index === 1 ? 0 : index);

function developFrame(to, from, t) {
  const image = ctx.createImageData(W, H);
  const out = image.data;
  const palette = INKS.map((ink) => colour(ink));
  const white = palette[1];
  // Per ink, this frame: how far its arrival has got (0 to 300 on the
  // `arrival` scale), the settled colour, the dark overshoot a pigment shows
  // just as it lands, and the grey shadow it shows before its turn.
  const shadow = clamp01(t / LAYERS[1][1]) * 0.55;
  const front = [];
  const landing = [];
  const waiting = [];
  for (let k = 0; k < INKS.length; k++) {
    const [a, b] = LAYERS[k];
    front[k] = clamp01((t - a) / (b - a)) * 300;
    landing[k] = mix(palette[k], palette[0], 0.4);
    const [r, g, bl] = palette[k];
    const y = r * 0.3 + g * 0.59 + bl * 0.11;
    waiting[k] = mix(white, [y, y, y], shadow);
  }
  // What a pixel shows before anything new reaches it: the old picture's
  // negative, fading toward paper as the pigments are pulled away from it.
  const fadeOld = 0.2 + clamp01(t / 420) * 0.6;
  const ghost = palette.map((_, k) => mix(palette[negative(k)], white, fadeOld));
  const clearFront = front[1];
  for (let i = 0, p = 0; i < to.length; i++, p += 4) {
    const k = to[i];
    const base = from ? ghost[from[i]] : white;
    // Each ink gets its own arrival order, so red and blue grains do not land
    // on the same pixels in the same sequence.
    const r = (arrival[i] + k * 97) & 255;
    let c;
    if (k < 2) {
      c = r < front[k] ? palette[k] : base;
    } else if (arrival[i] >= clearFront) {
      c = base;
    } else {
      const f = front[k];
      c = r < f - 44 ? palette[k] : r < f ? landing[k] : waiting[k];
    }
    const n = grain[i];
    out[p] = c[0] + n;
    out[p + 1] = c[1] + n;
    out[p + 2] = c[2] + n;
    out[p + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
}

function develop(to, from) {
  return new Promise((resolve) => {
    const started = performance.now();
    const step = (now) => {
      const t = now - started;
      developFrame(to, from, t);
      if (t < DEVELOP_MS) requestAnimationFrame(step);
      else { paint(to); resolve(); }
    };
    requestAnimationFrame(step);
  });
}

// The Spectra 6 update, compressed from about 15 seconds to about one: the old
// picture goes negative, then the new one develops over its fading ghost, ink
// by ink. No blank flash in between: the grains replace the old picture
// directly, the way the pigments do.
async function waveform(from, to) {
  if (from) { paint(from, { invert: true }); await sleep(200); }
  await develop(to, from);
}

let shown = null; // indices on the glass
let busy = false;
let queued = null;

function showCanvas() {
  if (!canvas.hidden) return;
  canvas.hidden = false;
  still.hidden = true;
}

function describe() {
  const scene = scenes[state.season];
  const page = state.page === 1
    ? 'the daily picture, sunrise and sunset, three bins and a four-day forecast'
    : 'page two, a 24-hour temperature curve with hourly rain and a week of ranges';
  const when = scene
    ? `${new Date(`${scene.date}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}, ${scene.condition}, ${Math.round(scene.low)} to ${Math.round(scene.high)} °C`
    : `a ${state.season} morning`;
  const style = scene && state.page === 1 ? ` The picture is a ${scene.style}.` : '';
  const language = state.lang === 'de' ? ' Labels in German.' : '';
  canvas.setAttribute('aria-label', `The foredogs dashboard on a reTerminal E1002: ${when}. It shows ${page}.${style}${language}`);
}

// One entry point for every change of what the panel shows. A change while
// the panel is repainting is not dropped but queued, and only the newest
// request survives, which is what pressing a real button twice does too.
async function repaint(mode = 'update') {
  if (busy) { queued = mode; return; }
  busy = true;
  paper.classList.add('busy');
  try {
    const target = await loadFrame(frameName());
    showCanvas();
    if (reducedMotion.matches || mode === 'instant') {
      paint(target);
    } else if (mode === 'clean') {
      for (const ink of [WHITE, BLACK, WHITE]) { fill(ink); await sleep(300); }
      await waveform(null, target);
    } else {
      await waveform(mode === 'wake' ? target : shown, target);
    }
    shown = target;
    describe();
    syncFollowers();
    preloadAround();
  } catch {
    // A frame that fails to load leaves the panel as it was, like dead WiFi
    // leaves the real one.
  } finally {
    busy = false;
    paper.classList.remove('busy');
    if (queued) { const next = queued; queued = null; repaint(next); }
  }
}

// Warm the frames a click away: the other page, the next morning, the other
// language. Each is 7 to 57 KB.
function preloadAround() {
  const next = SEASONS[(SEASONS.indexOf(state.season) + 1) % SEASONS.length];
  const around = [
    { ...state, page: 3 - state.page },
    { ...state, season: next, page: 1 },
    { ...state, lang: state.lang === 'en' ? 'de' : 'en' },
  ];
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 400));
  idle(() => around.forEach((s) => loadFrame(frameName(s)).catch(() => {})));
}

// ---------- panel controls ----------

const controls = $('#demo-controls');

function syncControls() {
  $$('[data-season]', controls).forEach((b) => b.setAttribute('aria-checked', String(b.dataset.season === state.season)));
  $$('[data-lang]', controls).forEach((b) => b.setAttribute('aria-checked', String(b.dataset.lang === state.lang)));
}

// Radio groups: arrow keys move within the group, as ARIA expects.
$$('.seg', controls).forEach((group) => {
  group.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    const buttons = $$('button', group);
    const step = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
    const next = buttons[(buttons.indexOf(document.activeElement) + step + buttons.length) % buttons.length];
    event.preventDefault();
    next.focus();
    next.click();
  });
});

controls.addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  const { season, lang } = button.dataset;
  if (season && season !== state.season) { state.season = season; syncControls(); repaint(); }
  if (lang && lang !== state.lang) { state.lang = lang; syncControls(); repaint(); }
});

$$('.rt-btn', panel).forEach((button) => {
  button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'page') {
      state.page = 3 - state.page;
      repaint();
    } else if (action === 'refresh') {
      // On the wall the green button repaints the same morning. Here it moves
      // to the next one, which is the part worth seeing; a new morning always
      // starts on page one, as the scheduled wake does.
      state.season = SEASONS[(SEASONS.indexOf(state.season) + 1) % SEASONS.length];
      state.page = 1;
      syncControls();
      repaint();
    } else if (action === 'clean') {
      repaint('clean');
    }
  });
});

// The gallery cards put their morning on the panel.
$$('[data-show]').forEach((button) => {
  button.addEventListener('click', () => {
    state.season = button.dataset.show;
    state.page = 1;
    syncControls();
    const top = $('#top');
    top.scrollIntoView({ behavior: reducedMotion.matches ? 'auto' : 'smooth', block: 'start' });
    setTimeout(() => repaint(), reducedMotion.matches ? 0 : 450);
  });
});

// ---------- sections that follow the panel ----------

const compare = $('#compare');
const compareRaw = $('#compare-raw');
const comparePanel = $('#compare-panel');
const range = $('#compare-range');
const pageTwo = $('#page-two');

range.addEventListener('input', () => compare.style.setProperty('--cut', `${range.value}%`));

const GALLERY_ALT = {
  winter: 'a dog in an apron shaping cinnamon rolls, snow on the rooftops outside',
  spring: 'a dog painting umbrellas onto postcards, a thunderstorm outside',
  summer: 'a dog splashing through a lake in watercolour',
  autumn: 'a dog leaping after wind-blown leaves, as a stained glass window',
};

let followed = '';
function syncFollowers() {
  const token = `${state.season}-${state.lang}`;
  if (token === followed) return;
  followed = token;
  compareRaw.src = `pictures/${state.season}.webp`;
  compareRaw.alt = `The picture as the model drew it: ${GALLERY_ALT[state.season]}.`;
  comparePanel.src = `pictures/${state.season}-panel.png`;
  pageTwo.src = `screens/${state.season}-${state.lang}-p2.png`;
}

// ---------- style prints ----------

// One print follows the pointer across the style index. It trails the pointer
// a little and leans into the direction it moves, the way a card held between
// two fingers would; switching rows cross-fades the picture inside it.
const styleIndex = $('#style-index');
const stylePreview = $('#style-preview');
if (styleIndex && stylePreview && window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
  const shots = $$('.style-frame img', stylePreview);
  const captionName = $('figcaption b', stylePreview);
  const captionWhat = $('figcaption i', stylePreview);
  const captionWhen = $('.cap-when', stylePreview);
  const GAP = 22;
  let front = 0;
  let shownKey = null;
  let visible = false;
  let raf = 0;
  const aim = { x: 0, y: 0 };
  const at = { x: 0, y: 0 };
  let tilt = 0;

  const size = () => stylePreview.getBoundingClientRect();

  // Keep the print on screen: above and to the right of the pointer by
  // default, flipped when that would leave the window.
  const aimAt = (x, y) => {
    const { width, height } = size();
    aim.x = x + GAP + width > window.innerWidth - 12 ? x - GAP - width : x + GAP;
    aim.y = y - GAP - height < 12 ? y + GAP : y - GAP - height;
  };

  const frame = () => {
    raf = 0;
    const still = reducedMotion.matches;
    const dx = aim.x - at.x;
    at.x = still ? aim.x : at.x + dx * 0.2;
    at.y = still ? aim.y : at.y + (aim.y - at.y) * 0.2;
    tilt = still ? 0 : tilt + (Math.max(-7, Math.min(7, dx * 0.06)) - tilt) * 0.15;
    stylePreview.style.setProperty('--x', `${at.x.toFixed(1)}px`);
    stylePreview.style.setProperty('--y', `${at.y.toFixed(1)}px`);
    stylePreview.style.setProperty('--tilt', `${tilt.toFixed(2)}deg`);
    const moving = Math.abs(aim.x - at.x) > 0.3 || Math.abs(aim.y - at.y) > 0.3 || Math.abs(tilt) > 0.05;
    if (visible && moving) raf = requestAnimationFrame(frame);
  };
  const follow = () => { if (!raf) raf = requestAnimationFrame(frame); };

  const pick = (row) => {
    const key = row.dataset.style;
    if (key === shownKey) return;
    shownKey = key;
    captionName.textContent = $('.style-name', row).textContent;
    captionWhat.textContent = row.dataset.activity || '';
    captionWhen.textContent = row.dataset.caption || '';
    const next = shots[1 - front];
    next.src = `pictures/styles/${key}.webp`;
    const swap = () => {
      if (shownKey !== key) return; // the pointer has already moved on
      next.classList.add('shown');
      shots[front].classList.remove('shown');
      front = 1 - front;
    };
    next.decode ? next.decode().then(swap, swap) : swap();
  };

  const show = (row, x, y, jump) => {
    pick(row);
    aimAt(x, y);
    if (!visible) {
      visible = true;
      if (jump) { at.x = aim.x; at.y = aim.y; }
      stylePreview.classList.add('on');
    }
    follow();
  };
  const hide = () => {
    visible = false;
    stylePreview.classList.remove('on');
  };

  styleIndex.addEventListener('pointermove', (event) => {
    const row = event.target.closest('.style-row');
    if (!row) { hide(); return; }
    show(row, event.clientX, event.clientY, !visible);
  });
  styleIndex.addEventListener('pointerleave', hide);
  window.addEventListener('scroll', () => { if (visible && !styleIndex.contains(document.activeElement)) hide(); }, { passive: true });

  // Keyboard: the print sits above the focused row instead of the pointer.
  styleIndex.addEventListener('focusin', (event) => {
    const row = event.target.closest('.style-row');
    if (!row || !row.matches(':focus-visible')) return;
    const rect = row.getBoundingClientRect();
    show(row, rect.left + rect.width * 0.55, rect.top, true);
  });
  styleIndex.addEventListener('focusout', hide);

  // The twelve prints are 20 to 40 KB each: fetch them once the index comes
  // near, so the first hover does not wait on the network.
  const warm = () => $$('.style-row', styleIndex).forEach((row) => { new Image().src = `pictures/styles/${row.dataset.style}.webp`; });
  if (window.IntersectionObserver) {
    const near = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      near.disconnect();
      warm();
    }, { rootMargin: '600px 0px' });
    near.observe(styleIndex);
  } else {
    warm();
  }
}

// ---------- first paint ----------

// The panel wakes into its first frame with a short update instead of
// swapping the plain image for the canvas mid-look.
syncControls();
describe();
function wake() {
  loadFrame(frameName()).then(() => repaint(reducedMotion.matches ? 'instant' : 'wake')).catch(() => {});
}
if (still.complete) wake(); else still.addEventListener('load', wake, { once: true });

// ---------- hero entrance ----------

const hero = $('.hero');
[...$('.hero-copy').children].forEach((el, i) => el.style.setProperty('--i', i));
requestAnimationFrame(() => hero.classList.add('is-on'));

// ---------- scroll reveal ----------

const items = $$('.reveal');
$$('.gallery, .more-grid, .duo, .steps').forEach((group) => {
  [...group.children].forEach((child, index) => {
    if (child.classList.contains('reveal')) child.style.setProperty('--i', index);
  });
});
const pending = new Set(items);
const show = (el) => { el.classList.add('in'); pending.delete(el); };
if (!window.IntersectionObserver) {
  items.forEach(show);
} else {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      show(entry.target);
      observer.unobserve(entry.target);
    });
  }, { rootMargin: '0px 0px -10% 0px', threshold: 0.05 });
  items.forEach((el) => observer.observe(el));
  // A very tall window can leave the last block inside the bottom margin at
  // full scroll; a plain on-screen check releases it.
  const sweep = () => {
    pending.forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight && r.bottom > 0) show(el);
    });
    if (!pending.size) {
      window.removeEventListener('scroll', sweep);
      window.removeEventListener('resize', sweep);
    }
  };
  window.addEventListener('scroll', sweep, { passive: true });
  window.addEventListener('resize', sweep, { passive: true });
}

// ---------- nav ----------

const nav = $('.top-nav');
const glow = $('.nav-glow', nav);
const navLinks = $$('a', nav);
const spied = navLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
let activeLink = null;
let hovered = null;
let scrollLock = false;
let lockTimer;

const light = (link) => {
  navLinks.forEach((other) => other.classList.toggle('lit', other === link));
  if (!link) { glow.style.opacity = '0'; return; }
  glow.classList.toggle('no-slide', glow.style.opacity !== '1');
  glow.style.width = `${link.offsetWidth}px`;
  glow.style.transform = `translateX(${link.offsetLeft}px)`;
  glow.style.opacity = '1';
};
const settle = () => light(hovered || activeLink);

navLinks.forEach((link) => {
  link.addEventListener('pointerenter', (event) => {
    if (event.pointerType !== 'mouse') return;
    hovered = link;
    settle();
  });
  link.addEventListener('click', () => {
    activeLink = link;
    scrollLock = true;
    clearTimeout(lockTimer);
    lockTimer = setTimeout(() => { scrollLock = false; }, 1200);
    settle();
  });
});
nav.addEventListener('pointerleave', () => { hovered = null; settle(); });
window.addEventListener('scroll', () => {
  if (!scrollLock) return;
  clearTimeout(lockTimer);
  lockTimer = setTimeout(() => { scrollLock = false; }, 160);
}, { passive: true });
window.addEventListener('resize', settle);
// The glow is measured off the links, and the links change width once Geist
// has loaded: measure again then, or the first highlight sits off-centre.
document.fonts?.ready.then(settle);

if (window.IntersectionObserver && spied.length) {
  const visible = new Map();
  const spy = new IntersectionObserver((entries) => {
    entries.forEach((entry) => visible.set(entry.target.id, entry.isIntersecting));
    if (scrollLock) return;
    const current = spied.find((section) => visible.get(section.id));
    activeLink = current ? navLinks.find((link) => link.getAttribute('href') === `#${current.id}`) : null;
    settle();
  }, { rootMargin: '-45% 0px -50% 0px' });
  spied.forEach((section) => spy.observe(section));
}

// ---------- copy ----------

$$('[data-copy-from]').forEach((button) => {
  const use = $('use', button);
  let timer;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById(button.dataset.copyFrom).textContent);
    } catch {
      return;
    }
    button.classList.add('copied');
    use.setAttribute('href', '#i-check');
    clearTimeout(timer);
    timer = setTimeout(() => {
      button.classList.remove('copied');
      use.setAttribute('href', '#i-copy');
    }, 1600);
  });
});
