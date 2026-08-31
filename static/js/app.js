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
  adGeneration: 0,
  adTimer: null,
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
  $('#modalRoot').hidden = true;
  $$('#modalRoot .modal').forEach(m => m.hidden = true);
}
$('#modalRoot').addEventListener('click', e => {
  if (e.target.dataset.close !== undefined || e.target.classList.contains('modal-backdrop')) closeModal();
});

/* ------------------------------------------------------------------ tabs */
function setTab(name) {
  if (!state.account && !['main', 'ads'].includes(name)) { openModal('modal-auth'); return; }
  if (name === 'admin' && !(state.account && state.account.is_admin)) return;
  state.tab = name;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
  if (name === 'coins') loadAds();
  if (name === 'rewards') { loadCatalogue(); loadOrders(); }
  if (name === 'admin') loadAdmin();
  renderInlineAds();
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
    const d = await api('/api/me', null); // noCaptcha flag no longer needed
    state.account = d.account;
    renderAccount();
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
  } catch (e) { $('#authErr').textContent = 'Login failed: ' + e.message; }
};

/* ------------------------------------------------------------------ ads tab */
let adsViewed = 0;
const _adSmartlinks = [
  'https://screwbedriddenheadline.com/ggxnb1mm?key=f9862fe342e3c188837c915e20b66334',
  'https://screwbedriddenheadline.com/ncq9qmr5k5?key=1a118a008303fcd7dd412e69532ebcbc',
  'https://screwbedriddenheadline.com/py7ycr6i37?key=4d31cab3fe4f7b7eddb6f1e9d3cd5ea9',
  'https://screwbedriddenheadline.com/t78vnwxx?key=1fdc549af3cd4fc2fcf16143c19d4a9e',
  'https://screwbedriddenheadline.com/tqbxiqs4k?key=bc091e88730e1c3d36bc96f002787282'
];
function refreshSponsorBtn() {
  const btn = $('#showAdBtn');
  if (!btn) return;
  btn.href = _adSmartlinks[adsViewed % _adSmartlinks.length];
}
function showAd() {
  adsViewed++;
  $('#adsCounter').textContent = adsViewed === 1 ? '1 sponsor opened' : adsViewed + ' sponsors opened';
  refreshSponsorBtn();
}
refreshSponsorBtn();
$('#showAdBtn').onclick = showAd;

/* ------------------------------------------------------------------ inline ads */
function renderInlineAds() {
  if (!window.adsbygoogle) {
    const s = document.createElement('script');
    s.async = true;
    s.src = 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-7937384871650239';
    s.crossOrigin = 'anonymous';
    document.head.appendChild(s);
  }
  document.querySelectorAll('.ad-slot:not(.ad-rendered)').forEach(slot => {
    const slotId = slot.dataset.adSlot;
    const fmt = slot.dataset.adFormat || 'auto';
    if (!slot.querySelector('ins.adsbygoogle')) {
      const ins = document.createElement('ins');
      ins.className = 'adsbygoogle';
      ins.style.cssText = 'display:inline-block;width:100%;height:auto';
      ins.setAttribute('data-ad-client', 'ca-pub-7937384871650239');
      ins.setAttribute('data-ad-slot', slotId);
      ins.setAttribute('data-ad-format', fmt);
      ins.setAttribute('data-full-width-responsive', 'true');
      slot.appendChild(ins);
    }
    try { (window.adsbygoogle = window.adsbygoogle || []).push({}); } catch (e) {}
    slot.classList.add('ad-rendered');
  });
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
    toast('Suspicious activity detected — session voided.', 'bad');
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
  const generation = ++state.adGeneration;
  state.adBusy = true;
  try {
    if (await detectAdblockClient()) {
      $('#adblockNotice').hidden = false;
      state.adBusy = false;
      return;
    }
    $('#adblockNotice').hidden = true;
    const d = await api('/api/ads/start', { pack });
    if (generation !== state.adGeneration) return;
    state.adRun = d.run;
    $('#adStage').hidden = false;
    await runNextAd(generation);
  } catch (e) {
    if (generation !== state.adGeneration) return;
    state.adBusy = false;
    toast('Could not start: ' + e.message, 'bad');
  }
}

$('#adAbort').onclick = () => {
  state.adGeneration += 1;
  if (state.adTimer) clearInterval(state.adTimer);
  state.adTimer = null;
  state.adBusy = false;
  $('#adStage').hidden = true;
  loadAds();
};

function renderCreative(c, host, slot) {
  host.innerHTML = '';
  const btn = (slot && slot.smartlink) ? `
    <a class="btn primary sponsor-btn" id="adSponsor" href="${slot.smartlink}"
       target="_blank" rel="noopener sponsored">Open sponsor</a>
    <small id="adSponsorNote">Opens in a new tab — come straight back, the timer keeps running.</small>` : '';
  if (c.type === 'adsterra') {
    host.innerHTML = `<div class="adsterra-slot">
      <span class="sponsor-kicker">SPONSORED</span>
      <h4>Advertisement</h4>
      <p>Keep this tab visible and focused while it runs.</p>${btn}
    </div>`;
    return;
  }
  host.innerHTML = `<div class="house-ad"><h4>Sponsored content</h4>
    <p>Keep this tab visible and focused until the timer reaches zero.</p>${btn}</div>`;
}

async function runNextAd(generation = state.adGeneration) {
  if (!state.adBusy || generation !== state.adGeneration) return;
  let slot;
  try { slot = await api('/api/ads/slot', {}); }
  catch (e) {
    if (generation !== state.adGeneration) return;
    state.adBusy = false; $('#adStage').hidden = true;
    if (e.message === 'finished') { toast('Run finished', 'ok'); }
    else toast('Ad error: ' + e.message, 'bad');
    loadAds(); refreshMe(); return;
  }
  if (!state.adBusy || generation !== state.adGeneration) return;

  $('#adCounter').textContent = `Ad ${slot.index} of ${slot.total}`;
  renderCreative(slot.creative, $('#adFrame'), slot);

  // Server-observed ad-block probe (the path is intentionally filter-list bait).
  let probeOk = true;
  try {
    const r = await fetch(slot.bait_url, { cache: 'no-store' });
    probeOk = r.ok;
  } catch (e) { probeOk = false; }
  if (!state.adBusy || generation !== state.adGeneration) return;
  if (!probeOk) {
    $('#adblockNotice').hidden = false;
    state.adBusy = false; $('#adStage').hidden = true;
    return;
  }

  let remaining = slot.seconds, seq = 0, done = false;
  const total = slot.seconds;
  let tick = null;
  const begin = () => {
    if (tick) return;
    tick = setInterval(async () => {
    if (!state.adBusy || generation !== state.adGeneration) {
      clearInterval(tick);
      return;
    }
    const visible = document.visibilityState === 'visible';
    const focused = document.hasFocus();
    if (visible && focused) remaining -= 1;
    $('#adTimer').textContent = Math.max(0, remaining) + 's';
    $('#adBar').style.width = Math.min(100, ((total - remaining) / total) * 100) + '%';
    $('#adNote').textContent = (visible && focused)
      ? 'Ad playing — do not switch tabs.'
      : 'Paused: bring this tab back to the front.';

    if (seq % slot.beat === 0) {
      try {
        const hb = await api('/api/ads/beat', { ticket: slot.ticket, visible, focused, seq });
        if (!state.adBusy || generation !== state.adGeneration) {
          clearInterval(tick);
          return;
        }
        if (hb.ok === false) {
          clearInterval(tick); state.adTimer = null;
          state.adBusy = false; $('#adStage').hidden = true;
          toast('Verification failed: ' + (hb.reason || ''), 'bad');
          return;
        }
        if (hb.paused) { remaining = total; }
      } catch (e) {
        clearInterval(tick); state.adTimer = null;
        if (generation !== state.adGeneration) return;
        state.adBusy = false; $('#adStage').hidden = true;
        return;
      }
    }
    seq++;

    if (remaining <= 0 && !done) {
      done = true; clearInterval(tick); state.adTimer = null;
      try {
        const blocked = await detectAdblockClient();
        if (!state.adBusy || generation !== state.adGeneration) return;
        const res = await api('/api/ads/complete', {
          ticket: slot.ticket, bait: slot.bait,
          flags: { blocked }
        });
        if (!state.adBusy || generation !== state.adGeneration) return;
        if (res.ok) {
          if (res.finished) {
            toast(`+${res.coins_awarded} coins added`, 'ok');
            state.adBusy = false; $('#adStage').hidden = true;
            await refreshMe(); await loadAds();
          } else {
            toast(`Ad ${res.done}/${res.required} done`, 'ok');
            // The next slot starts automatically, so 5- and 10-ad packs are
            // watched back to back without another button click.
            setTimeout(() => {
              if (state.adBusy && generation === state.adGeneration) runNextAd(generation);
            }, 600);
          }
        } else if (res.state === 'adblock') {
          $('#adblockNotice').hidden = false;
          state.adBusy = false; $('#adStage').hidden = true;
        } else {
          state.adBusy = false; $('#adStage').hidden = true;
          toast('Blocked: ' + res.reason, 'bad');
        }
      } catch (e) {
        if (generation !== state.adGeneration) return;
        state.adBusy = false; $('#adStage').hidden = true;
      }
    }
  }, 1000);
    state.adTimer = tick;
  };
  // the smartlink button IS the ad: the countdown only starts once the
  // sponsor has actually been opened
  const sponsor = $('#adSponsor');
  if (slot.smartlink && sponsor) {
    sponsor.addEventListener('click', () => {
      sponsor.classList.add('opened');
      const n = $('#adSponsorNote');
      if (n) n.textContent = 'Sponsor opened — ad timer running.';
      begin();
    });
    $('#adNote').textContent = 'Press "Open sponsor" — the ad timer starts when you do. ' +
                               'Keep this tab visible and focused.';
  } else {
    begin();
  }
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

function newNonce() {
  // unique per submit — the server only accepts each id once, so a
  // double-click, a retry or a second device can never double-charge.
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'n' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

async function submitOrder() {
  const link = $('#orderLink').value.trim();
  if (!link) return;
  $('#orderSponsorGate').hidden = true;
  $('#orderSubmit').disabled = true;
  try {
    const d = await api('/api/rewards/order', {
      platform: state.platform, service: state.service, link, nonce: newNonce()
    });
    toast(`Order #${d.order.id} queued${d.order.target ? ` → target ${d.order.target}` : ''}`, 'ok');
    $('#orderLink').value = '';
    await refreshMe(); await loadOrders();
  } catch (err) { toast(err.message, 'bad'); }
  $('#orderSubmit').disabled = false;
}

$('#orderForm').addEventListener('submit', async e => {
  e.preventDefault();
  const link = $('#orderLink').value.trim();
  if (!link || $('#orderSubmit').disabled) return;
  // show the sponsor gate instead of submitting directly
  const smartlinks = [
    'https://screwbedriddenheadline.com/ggxnb1mm?key=f9862fe342e3c188837c915e20b66334',
    'https://screwbedriddenheadline.com/ncq9qmr5k5?key=1a118a008303fcd7dd412e69532ebcbc',
    'https://screwbedriddenheadline.com/py7ycr6i37?key=4d31cab3fe4f7b7eddb6f1e9d3cd5ea9',
    'https://screwbedriddenheadline.com/t78vnwxx?key=1fdc549af3cd4fc2fcf16143c19d4a9e',
    'https://screwbedriddenheadline.com/tqbxiqs4k?key=bc091e88730e1c3d36bc96f002787282'
  ];
  const url = smartlinks[Math.floor(Math.random() * smartlinks.length)];
  const gate = $('#orderSponsorGate');
  const btn = $('#orderSponsorBtn');
  const note = $('#orderSponsorNote');
  btn.href = url;
  note.textContent = 'Opens in a new tab — come straight back, your order will submit automatically.';
  gate.hidden = false;
  btn.onclick = () => {
    btn.classList.add('opened');
    note.textContent = 'Sponsor opened — order submitting now…';
    submitOrder();
  };
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
      ${statCard('Orders running', d.orders_running)}
      ${statCard('Orders total (done)', d.orders_completed)}
      ${statCard('Database', d.database)}
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

refreshMe(false).then(() => { if (state.account) loadCatalogue(); renderInlineAds(); });
renderInlineAds();
})();
