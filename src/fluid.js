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
    this.wideWaveStarted = null;
    this.wideWaveOriginX = null;
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
    const waves = this.get().waves || {};
    const radius = Math.max(1, Math.min(8, Number(waves.success_drop_size ?? BEAD_R)));
    this.beads.push({ x: 0.5, y: 0, vy: 0, r: radius * strength, age: 0, small: false });
  }

  drip(radius = 1.9) {
    this.beads.push({ x: 0.22 + Math.random() * 0.56, y: 0, vy: 0, r: radius, age: 0, small: true });
  }

  quiet(st) {
    return !this.beads.length && this.reach < 0.05 && this.sag < 0.05 && !(st.playing && st.edge);
  }

  loop(now) {
    requestAnimationFrame(this.loop);
    const st = this.get();
    const waves = st.waves || {};
    const reducedMotion = waves.respect_reduced_motion !== false && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
    const ambientAmplitude = Math.max(0, Math.min(3, Number(waves.ambient_amplitude ?? 1)));
    const ambientSpeed = Math.max(0.2, Math.min(3, Number(waves.ambient_speed ?? 1)));

    // Тянемся к курсору, если он под островом и недалеко.
    let reachTarget = 0;
    if (!reducedMotion && waves.cursor_enabled !== false && this.cursor && now - this.cursor.at < 600 && st.pill) {
      const rect = this.cv.getBoundingClientRect();
      const k = rect.width / W || 1;
      const cx = (this.cursor.x - rect.left) / k, cy = (this.cursor.y - rect.top) / k;
      if (cy > -2 && cy < 150 && cx > a - 60 && cx < b + 60) {
        reachTarget = Math.max(0, Math.min(24, Number(waves.cursor_strength ?? 11))) * (1 - Math.max(0, cy) / 150);
        const tx = Math.max(a + 24, Math.min(b - 24, cx));
        this.reachX = this.reachX < 0 ? tx : this.reachX + (tx - this.reachX) * 0.25 * dt;
      }
    }
    this.reach += (reachTarget - this.reach) * 0.14 * dt;
    const sagTarget = !reducedMotion && st.away && st.pill && waves.away_sag_enabled !== false
      ? Math.max(0, Math.min(18, Number(waves.away_sag_strength ?? 7))) : 0;
    this.sag += (sagTarget - this.sag) * 0.03 * dt;

    const bands = st.bands || [];
    const peakCount = Math.max(4, Math.min(32, Number(waves.music_peak_count ?? 32)));
    const n = Math.max(1, Math.min(peakCount, bands.length || 12));
    const musicGain = Math.max(0, Math.min(2, Number(waves.music_gain ?? 1)));
    const musicAttack = Math.max(0.05, Math.min(1, Number(waves.music_attack ?? 0.55)));
    const musicRelease = Math.max(0.05, Math.min(1, Number(waves.music_release ?? 0.18)));
    for (let i = 0; i < n; i++) {
      const sourceIndex = bands.length ? Math.min(bands.length - 1, Math.floor((i + 0.5) * bands.length / n)) : i;
      const target = !reducedMotion && st.playing && st.edge && st.pill ? Math.max(0, Math.min(1.8, (Number(bands[sourceIndex]) || 0) * musicGain)) : 0;
      this.spikes[i] += (target - this.spikes[i]) * (target > this.spikes[i] ? musicAttack : musicRelease) * dt;
    }

    const rainInterval = Math.max(1, Math.min(12, Number(waves.rain_interval_s ?? 4))) * 1000;
    if (!reducedMotion && waves.rain_enabled !== false && st.rain && now - this.lastDrip > rainInterval * (0.65 + Math.random() * 0.7)) {
      this.lastDrip = now;
      this.drip(Math.max(0.8, Math.min(4, Number(waves.rain_drop_size ?? 1.9))));
    }

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
      let y = 1.1 + ambientAmplitude * (0.7 * Math.sin(t * 1.3 * ambientSpeed + x / 37) + 0.4 * Math.sin(t * 0.7 * ambientSpeed - x / 23));
      if (this.reach > 0.02) y += this.reach * gauss(x - this.reachX, 20);
      if (this.sag > 0.02) y += this.sag * gauss(x - W / 2, span * 0.3) * (1 + 0.08 * Math.sin(t * 0.9));
      for (let i = 0; i < n; i++) {
        const v = this.spikes[i];
        if (v > 0.02) y += v * 65 * gauss(x - (a + ((i + 0.5) / n) * span), (span / n) * Math.max(0.1, Math.min(0.8, Number(waves.music_peak_width ?? 0.3))));
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
      ctx.shadowBlur = Math.max(0, Math.min(24, Number(waves.music_glow ?? 10)));
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
    const waves = st.waves || {};
    const hiddenNow = !!st.hidden;
    const justCollapsed = hiddenNow && !this.wasHidden;
    this.wasHidden = hiddenNow;
    const reducedMotion = waves.respect_reduced_motion !== false && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (waves.full_width_enabled === false || reducedMotion) {
      this.wideWaveStarted = null;
      this.wideWaveOriginX = null;
    }
    if (justCollapsed && st.playing && waves.full_width_enabled !== false && !reducedMotion) {
      this.wideWaveStarted = now;
      this.wideWaveOriginX = null;
    }
    const duration = Math.max(0.8, Math.min(8, Number(waves.full_width_duration_s ?? 3))) * 1000;
    if (this.wideWaveStarted === null || now - this.wideWaveStarted >= duration) {
      this.wideWaveStarted = null;
      this.wideWaveOriginX = null;
      cv.classList.remove("active");
      ctx.clearRect(0, 0, cv.width, cv.height);
      return;
    }

    const width = Math.max(1, window.innerWidth);
    const height = H;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(width * dpr) || cv.height !== Math.round(height * dpr)) {
      cv.width = Math.round(width * dpr);
      cv.height = Math.round(height * dpr);
      cv.style.width = width + "px";
      cv.style.height = height + "px";
    }
    cv.classList.add("active");

    const count = Math.round(Math.max(8, Math.min(64, Number(waves.full_width_density ?? 32))));
    const elapsed = now - this.wideWaveStarted;
    const travel = Math.max(0, Math.min(1, elapsed / duration));
    if (this.wideWaveOriginX === null) {
      const canvasRect = cv.getBoundingClientRect();
      const islandRect = this.isl.getBoundingClientRect();
      const viewScale = canvasRect.width / width || 1;
      this.wideWaveOriginX = Math.max(0, Math.min(width,
        (islandRect.left + islandRect.width / 2 - canvasRect.left) / viewScale));
    }
    const originX = this.wideWaveOriginX;
    const waveWidth = Math.max(8, width / count * Math.max(0.5, Math.min(3.5, Number(waves.full_width_front_width ?? 1.8))));
    const pulseHz = Math.max(0.2, Math.min(3, Number(waves.full_width_pulse_hz ?? 1)));
    const amplitude = Math.max(0.2, Math.min(1.8, Number(waves.full_width_amplitude ?? 1)));
    const second = now / 1000 * Math.PI * 2 * pulseHz;
    const ambientAmplitude = Math.max(0, Math.min(3, Number(waves.ambient_amplitude ?? 1)));
    const ambientSpeed = Math.max(0.2, Math.min(3, Number(waves.ambient_speed ?? 1)));
    const span = Math.max(1, width);
    const spikes = Array.from({ length: count }, (_, i) => {
      const x = ((i + 0.5) / count) * span;
      const distance = x < originX
        ? Math.abs(x - originX) - originX * travel
        : Math.abs(x - originX) - (width - originX) * travel;
      const envelope = Math.exp(-(distance ** 2) / (2 * waveWidth ** 2));
      const fade = 1 - travel;
      return (0.5 + 0.5 * Math.sin(second + i * 0.55)) * envelope * fade * amplitude;
    });
    const waveY = (x) => {
      const taper = Math.pow(Math.max(0, Math.sin(Math.PI * x / span)), 0.7);
      let y = 1.1 + ambientAmplitude * (0.7 * Math.sin(second * 0.65 * ambientSpeed + x / 37) + 0.4 * Math.sin(second * 0.35 * ambientSpeed - x / 23));
      for (let i = 0; i < count; i++) {
        if (spikes[i] > 0.02) y += spikes[i] * 65 * gauss(x - ((i + 0.5) / count) * span, (span / count) * 0.3);
      }
      return y * taper;
    };
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.beginPath();
    ctx.moveTo(0, 0);
    for (let x = 0; x <= width; x += 2) ctx.lineTo(x, waveY(x));
    ctx.lineTo(width, 0);
    ctx.closePath();
    ctx.fillStyle = st.bg;
    ctx.fill();
    const gradient = ctx.createLinearGradient(0, 0, 0, 22);
    gradient.addColorStop(0, "rgba(0,0,0,0)");
    const accent = /^#[0-9a-f]{6}$/i.test(waves.color || "") ? waves.color : st.accent;
    gradient.addColorStop(0.35, accent);
    gradient.addColorStop(1, accent);
    ctx.fillStyle = gradient;
    ctx.shadowColor = accent;
    ctx.shadowBlur = Math.max(0, Math.min(24, Number(waves.full_width_glow ?? 10)));
    ctx.fill();
    ctx.shadowBlur = 0;
  }
}
