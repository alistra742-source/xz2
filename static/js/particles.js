/* Live flow-field background with drifting coins. */
(() => {
  const cv = document.getElementById('bg');
  if (!cv) return;
  const ctx = cv.getContext('2d', { alpha: true });
  let W = 0, H = 0, DPR = Math.min(window.devicePixelRatio || 1, 3);
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const dots = [];
  const coins = [];
  let t = 0;

  function resize() {
    W = cv.clientWidth = window.innerWidth;
    H = cv.clientHeight = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    build();
  }

  function build() {
    dots.length = 0; coins.length = 0;
    const area = W * H;
    const nDots = Math.min(220, Math.round(area / 9000));
    const nCoins = Math.min(26, Math.max(9, Math.round(area / 78000)));
    for (let i = 0; i < nDots; i++) {
      dots.push({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 1.7 + 0.5,
        s: Math.random() * 0.5 + 0.15,
        h: Math.random() * 60 + 190,
        a: Math.random() * 0.5 + 0.15
      });
    }
    for (let i = 0; i < nCoins; i++) {
      coins.push({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 12 + 9,
        s: Math.random() * 0.35 + 0.12,
        spin: Math.random() * Math.PI * 2,
        spd: (Math.random() * 0.02 + 0.008) * (Math.random() < .5 ? -1 : 1),
        drift: Math.random() * 0.5 - 0.25,
        a: Math.random() * 0.35 + 0.35
      });
    }
  }

  // flow field: smooth pseudo-noise so particles stream in currents
  function flow(x, y) {
    return Math.sin(x * 0.0016 + t * 0.00035) * 1.3 +
           Math.cos(y * 0.0021 - t * 0.00028) * 1.1;
  }

  function drawCoin(c) {
    const w = Math.abs(Math.cos(c.spin)) * c.r + 2.5;   // rim rotation illusion
    ctx.save();
    ctx.translate(c.x, c.y);
    ctx.globalAlpha = c.a;
    const g = ctx.createLinearGradient(-w, -c.r, w, c.r);
    g.addColorStop(0, '#fff0b8'); g.addColorStop(.45, '#ffc94d');
    g.addColorStop(.75, '#e79c17'); g.addColorStop(1, '#b9760a');
    ctx.beginPath();
    ctx.ellipse(0, 0, w, c.r, 0, 0, Math.PI * 2);
    ctx.fillStyle = g; ctx.fill();
    ctx.lineWidth = 1.4; ctx.strokeStyle = 'rgba(255,247,214,.75)'; ctx.stroke();
    if (w > c.r * 0.45) {
      ctx.globalAlpha = c.a * 0.85;
      ctx.fillStyle = '#8a5a06';
      ctx.font = `700 ${Math.round(c.r * 1.05)}px Segoe UI, sans-serif`;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('¢', 0, 1);
    }
    ctx.restore();
  }

  function frame() {
    t += 16;
    ctx.clearRect(0, 0, W, H);

    // link lines between nearby dots
    ctx.lineWidth = 1;
    for (let i = 0; i < dots.length; i++) {
      const p = dots[i];
      const ang = flow(p.x, p.y);
      p.x += Math.cos(ang) * p.s; p.y += Math.sin(ang) * p.s + p.s * 0.5;
      if (p.x < -20) p.x = W + 20; if (p.x > W + 20) p.x = -20;
      if (p.y > H + 20) { p.y = -20; p.x = Math.random() * W; }
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${p.h},90%,72%,${p.a})`;
      ctx.fill();
      for (let j = i + 1; j < i + 6 && j < dots.length; j++) {
        const q = dots[j];
        const dx = p.x - q.x, dy = p.y - q.y, d2 = dx * dx + dy * dy;
        if (d2 < 12000) {
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = `rgba(140,170,255,${0.10 * (1 - d2 / 12000)})`;
          ctx.stroke();
        }
      }
    }

    for (const c of coins) {
      const ang = flow(c.x * 0.6, c.y * 0.6);
      c.x += Math.cos(ang) * c.s + c.drift;
      c.y -= c.s * 1.15;
      c.spin += c.spd;
      if (c.y < -40) { c.y = H + 40; c.x = Math.random() * W; }
      if (c.x < -40) c.x = W + 40; if (c.x > W + 40) c.x = -40;
      drawCoin(c);
    }
    requestAnimationFrame(frame);
  }

  window.addEventListener('resize', resize, { passive: true });
  resize();
  if (!reduce) requestAnimationFrame(frame);
  else { /* static single paint */ ctx.clearRect(0, 0, W, H); coins.forEach(drawCoin); }
})();
