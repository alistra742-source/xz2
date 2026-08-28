/* COINFLOW front-end */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  account: null,
  tab: 'main',
  catalogue: null,
  platform: null,
  service: null,
  adRun: null,
  adBusy: false,
  captchaOpen: false,
  captcha: null,
  camsOn: false,
  timers: {}
};

/* ------------------------------------------------------------------ api */
async function api(path, body, opts = {}) {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin'
  });
  let data = {};
  try { data = await res.json(); } catch (e) {}
  if (res.status === 401) { state.account = null; renderAccount(); openModal('modal-auth'); throw new Error('login-required'); }
  if (res.status === 403 && data.error === 'captcha-required' && !opts.noCaptcha) {
    openCaptcha(true); throw new Error('captcha-required');
  }
  if (!res.ok) throw new Error(data.error || ('http-' + res.status));
  return data;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(8px)'; }, 4200);
  setTimeout(() => el.remove(), 4800);
}

/* ------------------------------------------------------------------ modals */
function openModal(id) {
  $('#modalRoot').hidden = false;
  $$('#modalRoot .modal').forEach(m => m.hidden = m.id !== id);
}
function closeModal() {
  if (state.captchaOpen) return;              // captchas are mandatory
  $('#modalRoot').hidden = true;
  $$('#modalRoot .modal').forEach(m => m.hidden = true);
}
$('#modalRoot').addEventListener('click', e => {
  if (e.target.dataset.close !== undefined || e.target.classList.contains('modal-backdrop')) closeModal();
});

/* ------------------------------------------------------------------ tabs */
function setTab(name) {
  if (!state.account && name !== 'main') { openModal('modal-auth'); return; }
  if (name === 'admin' && !(state.account && state.account.is_admin)) return;
  state.tab = name;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  if (name === 'coins') loadAds();
  if (name === 'rewards') { loadCatalogue(); loadOrders(); }
  if (name === 'admin') loadAdmin();
}
$('#tabs').addEventListener('click', e => {
  const t = e.target.closest('.tab'); if (t) setTab(t.dataset.tab);
});

/* ------------------------------------------------------------------ account */
function renderAccount() {
  const box = $('#accountBox');
  const a = state.account;
  $$('.tab').forEach(t => {
    if (['coins', 'rewards'].includes(t.dataset.tab)) t.classList.toggle('locked', !a);
  });
  $('.tab[data-tab="admin"]').hidden = !(a && a.is_admin);
  $('#pinnedNotice').hidden = !!a;
  $('#heroCoins').textContent = a ? a.coins : '—';

  if (!a) {
    box.innerHTML = '<button class="btn ghost" id="openAuth">Log in / Sign up</button>';
    $('#openAuth').onclick = () => openModal('modal-auth');
    if (state.tab !== 'main') setTab('main');
    return;
  }
  box.innerHTML = `
    <span class="coin-pill"><b id="coinVal">${a.coins}</b> coins</span>
    <span class="user-pill">@<b>${a.username}</b></span>
    <button class="btn ghost small" id="logoutBtn">Log out</button>`;
  $('#logoutBtn').onclick = async () => {
    await api('/api/logout', {}); state.account = null; renderAccount(); toast('Logged out');
  };
}

async function refreshMe(silent = true) {
  try {
    const d = await api('/api/me', null, { noCaptcha: true });
    state.account = d.account;
    renderAccount();
    if (d.account && d.account.captcha_due && !state.captchaOpen) openCaptcha(true);
  } catch (e) { if (!silent) console.warn(e); }
}

/* ---------------------------------------------------------- signup / login */
$('#pinnedCta').onclick = () => openModal('modal-confirm');
$('#openAuth').onclick = () => openModal('modal-auth');
$('#createAccountBtn').onclick = () => openModal('modal-confirm');

$('#confirmCreate').onclick = async () => {
  try {
    const d = await api('/api/account/new', {});
    $('#keyBox').textContent = d.key;
    $('#keySaved').checked = false;
    $('#goLogin').disabled = true;
    openModal('modal-key');
  } catch (e) { toast('Could not create a key: ' + e.message, 'bad'); }
};
$('#keySaved').onchange = e => { $('#goLogin').disabled = !e.target.checked; };
$('#copyKey').onclick = async () => {
  const key = $('#keyBox').textContent;
  try { await navigator.clipboard.writeText(key); toast('Key copied — store it safely!', 'ok'); }
  catch (e) {
    const ta = document.createElement('textarea'); ta.value = key; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove(); toast('Key copied', 'ok');
  }
};
$('#downloadKey').onclick = () => {
  const blob = new Blob([$('#keyBox').textContent + '\n\nKeep this file safe. It IS your account.'],
                        { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'coinflow-key.txt'; a.click();
};
$('#goLogin').onclick = () => { $('#loginKey').value = $('#keyBox').textContent; openModal('modal-auth'); };

$('#loginBtn').onclick = async () => {
  const key = $('#loginKey').value.trim();
  $('#authErr').textContent = '';
  if (key.length < 20) { $('#authErr').textContent = 'That key looks too short.'; return; }
  try {
    const d = await api('/api/login', { key });
    state.account = d.account;
    renderAccount();
    closeModal();
    toast(d.created ? `Account created: @${d.account.username}` : `Welcome back, @${d.account.username}`, 'ok');
    openCaptcha(true);
  } catch (e) { $('#authErr').textContent = 'Login failed: ' + e.message; }
};

/* ------------------------------------------------------------------ captcha */
async function openCaptcha(mandatory) {
  if (state.captchaOpen) return;
  state.captchaOpen = true;
  $('#capClose').style.display = mandatory ? 'none' : '';
  openModal('modal-captcha');
  await newCaptcha();
}

let capReady = false, capTrace = [], capMoved = false, capTrusted = true, capT0 = 0;

async function newCaptcha() {
  const img = $('#capImg'), item = $('#capItem'), itemImg = $('#capItemImg');
  capReady = false; capTrace = []; capMoved = false; capTrusted = true;
  $('#capDone').disabled = true;
  item.style.visibility = 'hidden';
  $('#capMsg').textContent = 'Loading challenge…';
  try {
    const d = await api('/api/captcha/new', { purpose: 'verify' }, { noCaptcha: true });
    state.captcha = d;
    itemImg.src = d.item;
    // the scene MUST be decoded before we size anything off it, otherwise
    // clientWidth is 0 and the draggable collapses to nothing
    await new Promise(res => { img.onload = res; img.onerror = res; img.src = d.scene; });
    if (img.clientWidth === 0) await new Promise(r => requestAnimationFrame(r));
    layoutItem(16, d.h - d.item_h - 18);
    item.style.visibility = '';
    capReady = true;
    $('#capSub').textContent = d.pending > 1
      ? `Solve ${d.pending} challenges to unlock your account.`
      : 'One more challenge to go.';
    $('#capMsg').textContent = 'Hold the framed object, drag it onto its target, then press Complete.';
  } catch (e) {
    $('#capMsg').textContent = 'Could not load a challenge — press New challenge.';
  }
}
$('#capNew').onclick = newCaptcha;
$('#capDone').onclick = submitCaptcha;

function sceneScale() {
  const img = $('#capImg');
  return (img.clientWidth || 1) / ((state.captcha && state.captcha.w) || 1);
}

/* place the draggable using SCENE coordinates for its top-left corner */
function layoutItem(sx, sy) {
  const k = sceneScale();
  $('#capItemImg').style.width = (state.captcha.item_w * k) + 'px';
  $('#capItem').style.left = (sx * k) + 'px';
  $('#capItem').style.top = (sy * k) + 'px';
}

/* centre of the sprite, expressed in scene coordinates */
function itemCenterScene() {
  const sr = $('#capScene').getBoundingClientRect();
  const ir = $('#capItemImg').getBoundingClientRect();
  const k = sceneScale() || 1;
  return { x: (ir.left + ir.width / 2 - sr.left) / k, y: (ir.top + ir.height / 2 - sr.top) / k };
}

(function dragSetup() {
  const item = $('#capItem'), scene = $('#capScene');
  let dragging = false, off = { x: 0, y: 0 };

  function down(e) {
    if (!capReady) return;
    dragging = true;
    if (e.isTrusted === false) capTrusted = false;
    if (!capTrace.length) capT0 = performance.now();
    const ir = item.getBoundingClientRect();
    off.x = e.clientX - ir.left;
    off.y = e.clientY - ir.top;
    try { item.setPointerCapture(e.pointerId); } catch (_) {}
    item.classList.add('dragging');
    $('#capMsg').textContent = 'Drop it on the target…';
    e.preventDefault();
  }

  function move(e) {
    if (!dragging) return;
    if (e.isTrusted === false) capTrusted = false;
    const sr = scene.getBoundingClientRect();
    const w = item.offsetWidth, h = item.offsetHeight;
    let x = e.clientX - sr.left - off.x;
    let y = e.clientY - sr.top - off.y;
    x = Math.max(-w * 0.35, Math.min(sr.width - w * 0.65, x));
    y = Math.max(-h * 0.35, Math.min(sr.height - h * 0.65, y));
    item.style.left = x + 'px';
    item.style.top = y + 'px';
    const c = itemCenterScene();
    capTrace.push([+c.x.toFixed(1), +c.y.toFixed(1), +(performance.now() - capT0).toFixed(1)]);
    if (capTrace.length > 900) capTrace.splice(0, 300);
    capMoved = true;
    e.preventDefault();
  }

  function up(e) {
    if (!dragging) return;
    dragging = false;
    item.classList.remove('dragging');
    try { item.releasePointerCapture(e.pointerId); } catch (_) {}
    if (capMoved && capTrace.length >= 6) {
      $('#capDone').disabled = false;
      $('#capMsg').textContent = 'Placed — press Complete to verify (or nudge it closer first).';
    } else {
      $('#capMsg').textContent = 'Hold the framed object and drag it onto its target.';
    }
  }

  item.addEventListener('pointerdown', down);
  item.addEventListener('pointermove', move);
  item.addEventListener('pointerup', up);
  item.addEventListener('pointercancel', up);
  item.addEventListener('lostpointercapture', up);
  item.addEventListener('dragstart', e => e.preventDefault());
  item.addEventListener('contextmenu', e => e.preventDefault());

  window.addEventListener('resize', () => {
    if (!state.captcha || !capReady) return;
    const c = itemCenterScene(), k = sceneScale();
    $('#capItemImg').style.width = (state.captcha.item_w * k) + 'px';
    requestAnimationFrame(() => {
      const w = $('#capItem').offsetWidth, h = $('#capItem').offsetHeight;
      $('#capItem').style.left = (c.x * k - w / 2) + 'px';
      $('#capItem').style.top = (c.y * k - h / 2) + 'px';
    });
  });
})();

async function submitCaptcha() {
  if (!capReady || !capMoved) return;
  const btn = $('#capDone');
  btn.disabled = true;
  const c = itemCenterScene();
  $('#capMsg').textContent = 'Checking…';
  try {
    const d = await api('/api/captcha/solve', {
      id: state.captcha.id, x: c.x, y: c.y, trace: capTrace, trusted: capTrusted
    }, { noCaptcha: true });
    if (d.ok && d.pending === 0) {
      state.captchaOpen = false;
      closeModal();
      toast('Verified — welcome in.', 'ok');
      await refreshMe();
    } else if (d.ok) {
      $('#capMsg').textContent = 'Correct. One more to go…';
      setTimeout(newCaptcha, 550);
    } else {
      $('#capMsg').textContent = d.reason === 'missed' || d.reason === 'wrong-container'
        ? 'Not the right spot — you now have 2 challenges again.'
        : 'That did not look like a human drag — try again.';
      setTimeout(newCaptcha, 900);
    }
  } catch (e) {
    $('#capMsg').textContent = 'Verification error — loading a new challenge…';
    setTimeout(newCaptcha, 900);
  }
}

/* ------------------------------------------------------------------ ads */
async function loadAds() {
  const d = await api('/api/ads/state');
  state.adRun = d.run;
  const box = $('#packs');
  box.innerHTML = '';
  d.packs.forEach(p => {
    const el = document.createElement('div');
    el.className = 'pack';
    el.innerHTML = `<div class="amount">+${p.coins}</div>
      <div class="desc">${p.label}${p.ads > 1 ? ` — ${p.ads} ads back to back` : ''}</div>
      <button class="btn primary block" data-pack="${p.id}">Start</button>`;
    box.appendChild(el);
  });
  $$('#packs button').forEach(b => b.onclick = () => startAds(b.dataset.pack));
  if (d.run && d.run.state === 'challenge') {
    toast('Suspicious activity detected — verify to continue.', 'bad');
    openCaptcha(true);
  }
}

async function detectAdblockClient() {
  // three independent local probes; the server runs its own probe too
  const bait = document.createElement('div');
  bait.className = 'adsbox ad-placement pub_300x250 ad-banner';
  bait.style.cssText = 'position:absolute;left:-9999px;height:12px;width:300px';
  document.body.appendChild(bait);
  await new Promise(r => setTimeout(r, 120));
  const hidden = bait.offsetHeight === 0 || bait.offsetParent === null ||
                 getComputedStyle(bait).display === 'none';
  bait.remove();
  let fetchBlocked = false;
  try {
    const r = await fetch('/pagead/js/adsbygoogle-probe.js', { cache: 'no-store' });
    fetchBlocked = !r.ok;
  } catch (e) { fetchBlocked = true; }
  return hidden || fetchBlocked;
}

async function startAds(pack) {
  if (state.adBusy) return;
  try {
    if (await detectAdblockClient()) { $('#adblockNotice').hidden = false; return; }
    $('#adblockNotice').hidden = true;
    const d = await api('/api/ads/start', { pack });
    state.adRun = d.run;
    state.adBusy = true;
    $('#adStage').hidden = false;
    await runNextAd();
  } catch (e) {
    state.adBusy = false;
    if (e.message !== 'captcha-required') toast('Could not start: ' + e.message, 'bad');
  }
}

$('#adAbort').onclick = () => {
  state.adBusy = false; $('#adStage').hidden = true; loadAds();
};

function renderCreative(c, host) {
  host.innerHTML = '';
  if (c.type === 'adsense') {
    const ins = document.createElement('ins');
    ins.className = 'adsbygoogle';
    ins.style.cssText = 'display:block;width:100%;height:280px';
    ins.setAttribute('data-ad-client', c.client);
    ins.setAttribute('data-ad-slot', c.slot);
    ins.setAttribute('data-ad-format', 'auto');
    ins.setAttribute('data-full-width-responsive', 'true');
    host.appendChild(ins);
    if (!window.__adsenseLoaded) {
      const s = document.createElement('script');
      s.async = true;
      s.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=' + c.client;
      s.crossOrigin = 'anonymous';
      document.head.appendChild(s);
      window.__adsenseLoaded = true;
    }
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {}
  } else {
    const spots = [
      ['Upgrade your setup', 'Sponsored — mechanical keyboards from $39.'],
      ['Host it in seconds', 'Sponsored — deploy any app with one click.'],
      ['Learn to edit like a pro', 'Sponsored — 30-day video editing bootcamp.'],
      ['Ship faster', 'Sponsored — the toolkit creators actually use.']
    ][(c.id || 1) - 1];
    host.innerHTML = `<div class="house-ad"><h4>${spots[0]}</h4><p>${spots[1]}</p></div>`;
  }
}

async function runNextAd() {
  if (!state.adBusy) return;
  let slot;
  try { slot = await api('/api/ads/slot', {}); }
  catch (e) {
    state.adBusy = false; $('#adStage').hidden = true;
    if (e.message === 'finished') { toast('Run finished', 'ok'); }
    else if (e.message !== 'captcha-required') toast('Ad error: ' + e.message, 'bad');
    loadAds(); refreshMe(); return;
  }

  $('#adCounter').textContent = `Ad ${slot.index} of ${slot.total}`;
  renderCreative(slot.creative, $('#adFrame'));

  // server-observed ad-block probe (path is on every filter list)
  let probeOk = true;
  try {
    const r = await fetch(slot.bait_url, { cache: 'no-store' });
    probeOk = r.ok;
  } catch (e) { probeOk = false; }
  if (!probeOk) {
    $('#adblockNotice').hidden = false;
    state.adBusy = false; $('#adStage').hidden = true;
    return;
  }

  let remaining = slot.seconds, seq = 0, done = false;
  const total = slot.seconds;
  const tick = setInterval(async () => {
    const visible = document.visibilityState === 'visible';
    const focused = document.hasFocus();
    if (visible && focused) remaining -= 1;
    $('#adTimer').textContent = Math.max(0, remaining) + 's';
    $('#adBar').style.width = Math.min(100, ((total - remaining) / total) * 100) + '%';
    $('#adNote').textContent = (visible && focused)
      ? 'Ad playing — do not switch tabs.'
      : '⏸ Paused: bring this tab back to the front.';

    if (seq % slot.beat === 0) {
      try {
        const hb = await api('/api/ads/beat', { ticket: slot.ticket, visible, focused, seq });
        if (hb.ok === false) {
          clearInterval(tick); state.adBusy = false; $('#adStage').hidden = true;
          toast('Verification needed: ' + (hb.reason || ''), 'bad');
          openCaptcha(true);
          return;
        }
        if (hb.paused) { remaining = total; }
      } catch (e) { clearInterval(tick); state.adBusy = false; $('#adStage').hidden = true; return; }
    }
    seq++;

    if (remaining <= 0 && !done) {
      done = true; clearInterval(tick);
      try {
        const res = await api('/api/ads/complete', {
          ticket: slot.ticket, bait: slot.bait,
          flags: { blocked: await detectAdblockClient() }
        });
        if (res.ok) {
          if (res.finished) {
            toast(`+${res.coins_awarded} coins added`, 'ok');
            state.adBusy = false; $('#adStage').hidden = true;
            await refreshMe(); await loadAds();
          } else {
            toast(`Ad ${res.done}/${res.required} done`, 'ok');
            setTimeout(runNextAd, 600);
          }
        } else if (res.state === 'adblock') {
          $('#adblockNotice').hidden = false;
          state.adBusy = false; $('#adStage').hidden = true;
        } else {
          state.adBusy = false; $('#adStage').hidden = true;
          toast('Blocked: ' + res.reason, 'bad');
          openCaptcha(true);
        }
      } catch (e) {
        state.adBusy = false; $('#adStage').hidden = true;
      }
    }
  }, 1000);
}

/* ------------------------------------------------------------------ rewards */
const PLAT_ICON = { tiktok: 'TT', instagram: 'IG', x: 'X', telegram: 'TG' };

async function loadCatalogue() {
  try {
    const c = await api('/api/rewards/catalogue', null, { noCaptcha: true });
    state.catalogue = c;
    renderPlatforms();
    renderServices();
  } catch (e) {}
}

function renderPlatforms() {
  const box = $('#platforms'); box.innerHTML = '';
  Object.entries(state.catalogue).forEach(([id, p]) => {
    if (id.startsWith('_')) return;
    const el = document.createElement('div');
    el.className = 'plat' + (state.platform === id ? ' sel' : '');
    el.innerHTML = `<span class="ic">${PLAT_ICON[id] || '--'}</span>${p.label}
      <small>${p.available ? 'available' : 'Soon will update'}</small>`;
    el.onclick = () => {
      state.platform = id; state.service = null;
      renderPlatforms(); renderServices();
    };
    box.appendChild(el);
  });
}

function showOrderForm() {
  const p = state.catalogue && state.catalogue[state.platform];
  const s = p && p.services.find(x => x.id === state.service);
  if (!p || !s || s.state !== 'up') { $('#orderForm').hidden = true; return; }
  const form = $('#orderForm');
  form.hidden = false;
  $('#orderLabel').textContent = `${s.label} · ${s.cost} coins — paste your ${p.label} link`;
  $('#orderLink').placeholder = state.platform === 'tiktok'
    ? 'https://www.tiktok.com/@user/video/123...'
    : 'https://www.instagram.com/reel/...';
  const demo = state.account && state.account.demo_available
    ? ' — your FREE demo try will cover this order (no coins spent).' : '';
  $('#orderHint').textContent = demo + (state.platform === 'tiktok'
    ? `We read your current ${s.unit} first, then push until it reaches current + ${s.amount}.`
    : `Delivered through our provider. One order per link every 5 minutes.`);
}

function renderServices() {
  const box = $('#services'); box.innerHTML = '';
  $('#orderForm').hidden = true;
  const p = state.platform && state.catalogue[state.platform];
  if (!p) return;
  if (!p.available) {
    box.innerHTML = `<div class="card"><h3>Soon will update</h3>
      <p>${p.label} rewards are not live yet. Check back shortly.</p></div>`;
    return;
  }
  p.services.forEach(s => {
    const el = document.createElement('div');
    el.className = 'svc' + (s.state !== 'up' ? ' down' : '') + (state.service === s.id ? ' sel' : '');
    el.innerHTML = `<div class="price">${s.cost}<span class="unit"> coins</span></div>
      <h3>${s.label}</h3>
      <span class="badge ${s.state}">${s.state === 'up' ? 'ONLINE' : s.state === 'down' ? 'DOWN — Soon will update' : 'CHECKING…'}</span>`;
    if (s.state === 'up') {
      el.onclick = () => { state.service = s.id; renderServices(); };
    }
    box.appendChild(el);
  });
  // the 5s catalogue refresh re-renders this list; keep the link form open
  // when a service is still selected instead of dropping it.
  showOrderForm();
}

$('#orderForm').addEventListener('submit', async e => {
  e.preventDefault();
  const link = $('#orderLink').value.trim();
  if (!link) return;
  $('#orderSubmit').disabled = true;
  try {
    const d = await api('/api/rewards/order', {
      platform: state.platform, service: state.service, link
    });
    toast(`Order #${d.order.id} queued${d.order.target ? ` → target ${d.order.target}` : ''}`, 'ok');
    $('#orderLink').value = '';
    await refreshMe(); await loadOrders();
  } catch (err) { toast(err.message, 'bad'); }
  $('#orderSubmit').disabled = false;
});

async function loadOrders() {
  try {
    const d = await api('/api/orders', null, { noCaptcha: true });
    const box = $('#ordersList');
    if (!d.orders.length) { box.innerHTML = '<p class="muted">No orders yet.</p>'; return; }
    box.innerHTML = d.orders.map(o => {
      const span = Math.max(1, o.target - o.baseline);
      const pct = o.target ? Math.min(100, Math.max(0, ((o.current - o.baseline) / span) * 100)) : 0;
      return `<div class="order">
        <div class="top">
          <b>#${o.id} · ${o.platform} ${o.service}</b>
          <span class="st ${o.status}">${o.status.toUpperCase()}</span>
        </div>
        <small>${o.link}</small>
        ${o.target ? `<small>${o.current} / ${o.target} (started at ${o.baseline})</small>
        <div class="bar"><i style="width:${pct}%"></i></div>` : ''}
        <small>${o.message || ''}</small>
      </div>`;
    }).join('');
  } catch (e) {}
}

$('#promoBtn').onclick = async () => {
  const code = $('#promoCode').value.trim();
  if (!code) return;
  try {
    const d = await api('/api/promo', { code });
    $('#promoCode').value = '';
    if (d.admin) {
      $('#promoMsg').textContent = 'Access granted.';
      await refreshMe();
      setTab('admin');
    } else {
      $('#promoMsg').textContent = `+${d.coins_added} coins added.`;
      toast(`+${d.coins_added} coins`, 'ok');
      await refreshMe();
    }
  } catch (e) { $('#promoMsg').textContent = e.message; }
};

/* ------------------------------------------------------------------ admin */
async function loadAdmin() {
  try {
    const d = await api('/api/admin/stats', null, { noCaptcha: true });
    $('#adminStats').innerHTML = `
      ${statCard('Accounts total', d.accounts_total)}
      ${statCard('Accounts created', d.accounts_created)}
      ${statCard('Coins used globally', d.coins_spent)}
      ${statCard('Coins earned', d.coins_earned)}
      ${statCard('Ads watched', d.ads_watched)}
      ${statCard('Orders completed', d.orders_done)}
      ${statCard('Monitor', d.monitor.monitor_running ? 'LIVE' : 'OFFLINE')}`;
    $('#engineLog').textContent = (d.logs || []).join('\n');
    $('#codeList').innerHTML = (d.codes || []).map(c =>
      `<div><b class="mono">${c.code}</b> — ${c.coins} coins · ${c.uses_left} uses left</div>`).join('');
    $('#adminOrders').innerHTML = (d.orders || []).map(o =>
      `<div class="order"><div class="top"><b>#${o.id} ${o.platform}/${o.service}</b>
       <span class="st ${o.status}">${o.status}</span></div>
       <small>${o.current}/${o.target} — ${o.message || ''}</small></div>`).join('');
  } catch (e) {}
  if (state.camsOn) loadCams();
}
const statCard = (label, value) =>
  `<div class="stat"><b>${value}</b><span>${label}</span></div>`;

$('#genBtn').onclick = async () => {
  try {
    const d = await api('/api/admin/promo', {
      coins: +$('#genCoins').value, uses: +$('#genUses').value, code: $('#genCode').value
    });
    $('#genOut').textContent = `${d.code} → ${d.coins} coins × ${d.uses} uses`;
    $('#genCode').value = '';
    loadAdmin();
  } catch (e) { $('#genOut').textContent = 'Error: ' + e.message; }
};

$('#camToggle').onclick = () => {
  state.camsOn = !state.camsOn;
  $('#camToggle').textContent = state.camsOn ? 'Hide' : 'Show';
  $('#cams').innerHTML = '';
  if (state.camsOn) loadCams();
};

async function loadCams() {
  try {
    const d = await api('/api/admin/cams', null, { noCaptcha: true });
    const box = $('#cams');
    const keys = d.cams.map(c => c.key);
    // create missing tiles
    d.cams.forEach(c => {
      let tile = box.querySelector(`[data-cam="${c.key}"]`);
      if (!tile) {
        tile = document.createElement('div');
        tile.className = 'cam';
        tile.dataset.cam = c.key;
        tile.innerHTML = `<img alt="${c.label}"><div class="meta"><span>${c.label}</span><span class="s"></span></div>`;
        box.appendChild(tile);
      }
      tile.querySelector('.s').textContent = c.status || '';
      if (c.has_image) {
        tile.querySelector('img').src = `/api/admin/cam/${c.key}.jpg?t=${Date.now()}`;
      }
    });
    [...box.children].forEach(t => { if (!keys.includes(t.dataset.cam)) t.remove(); });
    if (!d.cams.length) box.innerHTML = '<p class="muted">No worker pages running yet.</p>';
  } catch (e) {}
}

/* ------------------------------------------------------------------ loops */
setInterval(() => { if (state.account) refreshMe(); }, 30000);
setInterval(() => { if (state.tab === 'rewards') { loadCatalogue(); loadOrders(); } }, 5000);
setInterval(() => { if (state.tab === 'admin') loadAdmin(); }, 5000);
setInterval(() => { if (state.tab === 'admin' && state.camsOn) loadCams(); }, 1000);

refreshMe(false).then(() => { if (state.account) loadCatalogue(); });
})();
