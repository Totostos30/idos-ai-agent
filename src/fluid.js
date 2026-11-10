// Жидкий край острова в духе Island: капсула «из феррожидкости».
// Нижний край тянется к курсору, колется шипами под музыку цветом обложки (низкие слева, высокие справа),
// роняет каплю, когда что-то удалось, провисает, когда вас нет, и подкапывает в дождь.
const H = 64;
const BEAD_R = 3.4;
const gauss = (d, s) => Math.exp(-(d * d) / (2 * s * s));

export class Fluid {
  constructor(island, canvas, getState, wideDropsCanvas) {
    this.isl = island;
    this.cv = canvas;
    this.ctx = canvas.getContext("2d");
    this.wideDropsCv = wideDropsCanvas;
    this.wideDropsCtx = wideDropsCanvas.getContext("2d");
    this.get = getState;
    this.cursor = null;
    this.reach = 0;
    this.reachX = -1;
    this.sag = 0;
    this.beads = [];
    this.spikes = new Float32Array(32);
    this.lastDrip = 0;
    this.last = performance.now();
    this.frame = 0;
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  setCursor(x, y) { this.cursor = { x, y, at: performance.now() }; }

  // Капля отрывается от середины края и падает (успешное действие, готовый ответ, скриншот).
  bead(strength = 1) {
    if (this.beads.length > 6) return;
    this.beads.push({ x: 0.5, y: 0, vy: 0, r: BEAD_R * strength, age: 0, small: false });
  }

  drip() {
    this.beads.push({ x: 0.22 + Math.random() * 0.56, y: 0, vy: 0, r: 1.9, age: 0, small: true });
  }

  quiet(st) {
    return !this.beads.length && this.reach < 0.05 && this.sag < 0.05 && !(st.playing && st.edge);
  }

  loop(now) {
    requestAnimationFrame(this.loop);
    const st = this.get();
    const dt = Math.min(48, now - this.last) / 16.7;
    this.frame++;
    this.drawWideDrops(st, now);
    if (!st.liquid) {
      if (this.cv.style.opacity !== "0") this.cv.style.opacity = "0";
      this.beads.length = 0;
      this.reach = 0;
      this.reachX = -1;
      this.sag = 0;
      return;
    }
    this.cv.style.opacity = "1";
    // В покое хватает 30 кадров в секунду: экономим батарею ноутбука.
    if (this.quiet(st) && this.frame % 2) return;
    this.last = now;

    const W = this.isl.offsetWidth;
    if (!W) return;
    const dpr = window.devicePixelRatio || 1;
    if (this.cv.width !== Math.round(W * dpr) || this.cv.height !== Math.round(H * dpr)) {
      this.cv.width = Math.round(W * dpr);
      this.cv.height = Math.round(H * dpr);
      this.cv.style.width = W + "px";
      this.cv.style.height = H + "px";
    }
    const cs = getComputedStyle(this.isl);
    const R = Math.min(parseFloat(cs.borderBottomLeftRadius) || 18, this.isl.offsetHeight / 2);
    const a = R * 0.75, b = W - R * 0.75, span = Math.max(1, b - a);
    const t = now / 1000;

    // Тянемся к курсору, если он под островом и недалеко.
    let reachTarget = 0;
    if (this.cursor && now - this.cursor.at < 600 && st.pill) {
      const rect = this.cv.getBoundingClientRect();
      const k = rect.width / W || 1;
      const cx = (this.cursor.x - rect.left) / k, cy = (this.cursor.y - rect.top) / k;
      if (cy > -2 && cy < 150 && cx > a - 60 && cx < b + 60) {
        reachTarget = 11 * (1 - Math.max(0, cy) / 150);
        const tx = Math.max(a + 24, Math.min(b - 24, cx));
        this.reachX = this.reachX < 0 ? tx : this.reachX + (tx - this.reachX) * 0.25 * dt;
      }
    }
    this.reach += (reachTarget - this.reach) * 0.14 * dt;
    this.sag += ((st.away && st.pill ? 7 : 0) - this.sag) * 0.03 * dt;

    const bands = st.bands || [];
    const n = Math.max(1, Math.min(32, bands.length || 12));
    for (let i = 0; i < n; i++) {
      const target = st.playing && st.edge && st.pill ? Math.min(1, bands[i] || 0) : 0;
      this.spikes[i] += (target - this.spikes[i]) * (target > this.spikes[i] ? 0.55 : 0.18) * dt;
    }

    if (st.rain && now - this.lastDrip > 2400 + Math.random() * 2600) { this.lastDrip = now; this.drip(); }

    const necks = [];
    for (const d of this.beads) {
      d.age += dt / 60;
      const grow = d.small ? 0.25 : 0.4;
      if (d.age < grow) necks.push([d.x * W, (d.age / grow) * (d.small ? 5 : 9), d.small ? 5 : 8]);
      else { d.vy += 0.32 * dt; d.y += d.vy * dt; }
    }
    this.beads = this.beads.filter((d) => d.y < H + 10);

    const f = (x) => {
      if (x <= a || x >= b) return 0;
      const taper = Math.pow(Math.sin(Math.PI * (x - a) / span), 0.7);
      let y = 1.1 + 0.7 * Math.sin(t * 1.3 + x / 37) + 0.4 * Math.sin(t * 0.7 - x / 23);
      if (this.reach > 0.02) y += this.reach * gauss(x - this.reachX, 20);
      if (this.sag > 0.02) y += this.sag * gauss(x - W / 2, span * 0.3) * (1 + 0.08 * Math.sin(t * 0.9));
      for (let i = 0; i < n; i++) {
        const v = this.spikes[i];
        if (v > 0.02) y += v * 65 * gauss(x - (a + ((i + 0.5) / n) * span), (span / n) * 0.3);
      }
      for (const [nx, h, s] of necks) y += h * gauss(x - nx, s);
      return y * taper;
    };

    const ctx = this.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.beginPath();
    ctx.moveTo(a, 0);
    for (let x = a; x <= b; x += 2) ctx.lineTo(x, f(x));
    ctx.lineTo(b, 0);
    ctx.closePath();
    ctx.fillStyle = st.bg;
    ctx.fill();
    if (st.playing && st.edge && st.pill) {
      // Кончики шипов окрашены в цвет обложки и светятся.
      const g = ctx.createLinearGradient(0, 0, 0, 16);
      g.addColorStop(0, "rgba(0,0,0,0)");
      g.addColorStop(0.35, st.accent);
      g.addColorStop(1, st.accent);
      ctx.save();
      ctx.globalAlpha = 0.95;
      ctx.shadowColor = st.accent;
      ctx.shadowBlur = 10;
      ctx.fillStyle = g;
      ctx.fill();
      ctx.restore();
    }
    for (const d of this.beads) {
      if (d.y <= 0 && d.age < (d.small ? 0.25 : 0.4)) continue;
      ctx.beginPath();
      const stretch = 1 + Math.min(0.5, d.vy * 0.05);
      ctx.ellipse(d.x * W, 6 + d.y, d.r, d.r * stretch, 0, 0, Math.PI * 2);
      ctx.globalAlpha = Math.max(0, 1 - d.y / H);
      ctx.fillStyle = d.small ? st.rainColor : st.bg;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  drawWideDrops(st, now) {
    const cv = this.wideDropsCv;
    const ctx = this.wideDropsCtx;
    if (!cv || !ctx) return;
    if (!st.hidden) {
      ctx.clearRect(0, 0, cv.width, cv.height);
      return;
    }

    const width = Math.max(1, window.innerWidth);
    const height = 16;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(width * dpr) || cv.height !== Math.round(height * dpr)) {
      cv.width = Math.round(width * dpr);
      cv.height = Math.round(height * dpr);
      cv.style.width = width + "px";
      cv.style.height = height + "px";
    }

    const spacing = 36;
    const count = Math.ceil(width / spacing) + 1;
    const second = now / 1000 * Math.PI * 2;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = st.accent;
    ctx.shadowColor = st.accent;
    ctx.shadowBlur = 6;
    for (let i = 0; i < count; i++) {
      const x = i * spacing;
      const phase = second + (x / width) * Math.PI * 2;
      const pulse = (Math.sin(phase) + 1) / 2;
      const y = 7 + pulse * 4;
      ctx.globalAlpha = 0.55 + pulse * 0.45;
      ctx.beginPath();
      ctx.arc(x, y, BEAD_R, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.shadowBlur = 0;
  }
}
