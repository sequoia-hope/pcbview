/* scale.js — life size: how many CSS px make a millimetre on this screen.
 *
 * A browser does not know how big its screen is (a CSS px is nominally 1/96
 * inch, and on no real screen is it), so the answer comes from one of:
 *
 *   - this screen's own setup, saved in localStorage: the user held a card to
 *     the screen, or typed the screen's diagonal;
 *   - the table below: an iPhone or iPad is known by its screen's size in CSS
 *     px and its pixel ratio, and Apple publishes its pixels per inch.
 *
 * A setup is kept per screen (its size in CSS px, so a laptop and the monitor
 * beside it each have their own) as device px per mm, so a browser that
 * zooms by changing devicePixelRatio (Chrome, Firefox) stays life size. A
 * page pinched on a phone is allowed for by visualViewport.scale.
 *
 *   PV.scale.get()      -> {pxmm, how, name} or null: CSS px per mm, now
 *   PV.scale.setup()    -> Promise of the same, after the dialog (null: cancelled)
 */
(() => {
  const PV = window.PV = window.PV || {};
  const KEY = 'pv-scale';
  const CARD = [85.6, 53.98];                     // ISO/IEC 7810 ID-1: a bank card

  // [CSS px short side, long side, pixel ratio, physical px across, ppi, name].
  // Physical px over ppi is the screen's width: the Plus phones and the minis
  // draw at 3x and scale down, so CSS px x ratio is not the panel's pixels.
  // 375x812 @3 is left out: the X, XS and 11 Pro (458 ppi) and the 12 and 13
  // mini (476, scaled) share it and differ by 8%.
  const APPLE = [
    [440, 956, 3, 1320, 460, 'iPhone 16 Pro Max / 17 Pro Max'],
    [402, 874, 3, 1206, 460, 'iPhone 16 Pro / 17 / 17 Pro'],
    [420, 912, 3, 1260, 460, 'iPhone Air'],
    [430, 932, 3, 1290, 460, 'iPhone 14 Pro Max / 15 Plus / 15 Pro Max / 16 Plus'],
    [393, 852, 3, 1179, 460, 'iPhone 14 Pro / 15 / 15 Pro / 16'],
    [390, 844, 3, 1170, 460, 'iPhone 12 / 13 / 14 / 16e'],
    [428, 926, 3, 1284, 458, 'iPhone 12 Pro Max / 13 Pro Max / 14 Plus'],
    [414, 896, 3, 1242, 458, 'iPhone XS Max / 11 Pro Max'],
    [414, 896, 2, 828, 326, 'iPhone XR / 11'],
    [414, 736, 3, 1080, 401, 'iPhone 6–8 Plus'],
    [375, 667, 2, 750, 326, 'iPhone 6–8 / SE'],
    [1032, 1376, 2, 2064, 264, 'iPad Pro 13″'],
    [834, 1210, 2, 1668, 264, 'iPad Pro 11″ (M4)'],
    [834, 1194, 2, 1668, 264, 'iPad Pro 11″'],
    [1024, 1366, 2, 2048, 264, 'iPad Pro 12.9″ / Air 13″'],
    [820, 1180, 2, 1640, 264, 'iPad Air 11″ / iPad'],
    [810, 1080, 2, 1620, 264, 'iPad'],
    [744, 1133, 2, 1488, 326, 'iPad mini'],
  ];

  const ratio = () => window.devicePixelRatio || 1;
  const pinch = () => (window.visualViewport && visualViewport.scale) || 1;
  const dims = () => [screen.width, screen.height].sort((a, b) => a - b);
  const screenKey = () => dims().join('x');
  const ios = () => /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (/Macintosh/.test(navigator.userAgent) && navigator.maxTouchPoints > 1);   // an iPad asking for the desktop site

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(all) {
    try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) { /* private window: this visit only */ }
  }
  let session = {};                               // when localStorage will not keep it

  function known() {
    if (!ios()) return null;
    const [w, h] = dims();
    const hit = APPLE.find(d => d[0] === w && d[1] === h && d[2] === Math.round(ratio()));
    return hit && { pxmm: hit[0] / (hit[3] / hit[4] * 25.4), how: 'known', name: hit[5] };
  }

  function get() {
    const mine = session[screenKey()] || load()[screenKey()];
    if (mine && mine.dpmm > 0) return { pxmm: mine.dpmm / ratio() / pinch(), how: mine.how, name: mine.name };
    const k = known();
    return k && { pxmm: k.pxmm / pinch(), how: k.how, name: k.name };
  }

  function keep(pxmm, how, name) {
    const rec = { dpmm: pxmm * pinch() * ratio(), how, name };
    session[screenKey()] = rec;
    const all = load();
    all[screenKey()] = rec;
    save(all);
  }
  function forget() {
    delete session[screenKey()];
    const all = load();
    delete all[screenKey()];
    save(all);
  }

  // ---- the dialog --------------------------------------------------------------
  let dlg = null;
  function setup() {
    if (!dlg) dlg = build();
    return dlg.open();
  }

  function build() {
    const d = document.createElement('dialog');
    d.className = 'pv-scale';
    d.innerHTML = `
      <form method="dialog">
        <h3>Life size on this screen</h3>
        <p class="sub" data-s="now"></p>
        <p>Hold a bank card (or any ID-1 card) flat against the screen and drag
        the slider until the outline matches its edges.</p>
        <div class="pv-card-box"><div class="pv-card-out"><span>85.6 × 54 mm</span></div></div>
        <input type="range" data-s="slide" min="1" max="20" step="0.005" aria-label="pixels per millimetre">
        <p>Or give the size of the screen, corner to corner:
        <label><input type="number" data-s="diag" min="1" max="200" step="0.1" inputmode="decimal"> inches</label>
        <span class="sub" data-s="px"></span></p>
        <p class="sub">Set it at 100% browser zoom. A phone with Display Zoom (iPhone) or a
        larger display size (Android) needs setting up here too.</p>
        <div class="cu-row">
          <button value="ok" class="on">Use this</button>
          <button value="reset" data-s="reset">Forget this screen</button>
          <button value="cancel">Cancel</button>
        </div>
      </form>`;
    (document.querySelector('section.vw') || document.body).appendChild(d);
    const $ = s => d.querySelector(`[data-s=${s}]`);
    const out = d.querySelector('.pv-card-out'), slide = $('slide'), diag = $('diag');
    let pxmm = 96 / 25.4, how = 'card', resolve = null;

    const diagPx = () => Math.hypot(screen.width, screen.height) / pinch();
    function show() {
      const tall = d.querySelector('.pv-card-box').clientWidth < CARD[0] * pxmm;   // turn the card to fit
      const [w, h] = tall ? [CARD[1], CARD[0]] : CARD;
      out.style.width = w * pxmm + 'px';
      out.style.height = h * pxmm + 'px';
      out.style.borderRadius = 3.18 * pxmm + 'px';                 // the card's own corners
      slide.value = pxmm;
    }
    slide.addEventListener('input', () => {
      pxmm = +slide.value; how = 'card';
      diag.value = (diagPx() / pxmm / 25.4).toFixed(1);
      show();
    });
    diag.addEventListener('input', () => {
      const v = parseFloat(diag.value);
      if (!(v > 0)) return;
      pxmm = diagPx() / (v * 25.4); how = 'diagonal';
      show();
    });

    d.addEventListener('close', () => {
      const v = d.returnValue;
      if (v === 'ok') keep(pxmm, how, how === 'card' ? 'matched to a card' : `a ${diag.value}″ screen`);
      else if (v === 'reset') forget();
      if (resolve) resolve(v === 'cancel' || !v ? null : get());
      resolve = null;
    });

    return {
      open() {
        const now = get();
        pxmm = now ? now.pxmm : 96 / 25.4;
        how = now && now.how === 'diagonal' ? 'diagonal' : 'card';
        $('now').textContent = now
          ? `Now: ${now.name}, ${(now.pxmm * 25.4).toFixed(0)} CSS px to the inch.`
          : 'This screen is not set up yet.';
        $('reset').hidden = !(session[screenKey()] || load()[screenKey()]);
        $('px').textContent = `(${screen.width} × ${screen.height} px)`;
        diag.value = (diagPx() / pxmm / 25.4).toFixed(1);
        d.returnValue = '';
        d.showModal();
        show();
        return new Promise(r => { resolve = r; });
      },
    };
  }

  PV.scale = { get, setup };
})();
