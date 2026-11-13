// Знак Νοῦς (гайдбук §12): морфинг-движок состояний агента.
// Один знак, параметры которого (радиусы, цвета, пунктиры, скорости) твинятся
// к целевому состоянию — состояния перетекают, а не сменяются кадрами.
// set(state) — "idle"|"listen"|"thinking"|"searching"|"working"|"speaking"|
// "waiting"|"error"|"done"|"transcribing"|"scn". Цвета — только токены (--b*/--ok/--err/--ink).

const R = (r, c, w, op, dA, dB, spd) => ({ r, c, w, op, dA, dB, spd: spd || 0 });
const D = (r, c, op, amp) => ({ r, c, op, amp: amp || 0 });
const lerp = (a, b, k) => a + (b - a) * k;

function readColors(root) {
  const css = getComputedStyle(root);
  const parse = (v) => {
    v = v.trim();
    if (v.startsWith("#")) return [parseInt(v.slice(1, 3), 16), parseInt(v.slice(3, 5), 16), parseInt(v.slice(5, 7), 16)];
    const m = v.match(/\d+/g);
    return m ? m.slice(0, 3).map(Number) : [245, 245, 247];
  };
  return { b1: parse(css.getPropertyValue("--b1") || css.getPropertyValue("--accent")),
    b2: parse(css.getPropertyValue("--b2") || css.getPropertyValue("--accent")),
    b3: parse(css.getPropertyValue("--b3") || css.getPropertyValue("--accent")),
    ok: parse(css.getPropertyValue("--ok")), err: parse(css.getPropertyValue("--err")),
    ink: parse(css.getPropertyValue("--ink") || css.getPropertyValue("--fg")),
    white: [245, 245, 247] };
}

const STATES = (C) => ({
  idle:         { r1: R(5.6, C.b1, 1.5, .9, 1, 0), r2: R(8.6, C.b2, 1.3, .55, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, .24, 1, 0), dot: D(2.8, C.ink, 1), glow: .12 },
  listen:       { r1: R(5.6, C.b1, 1.5, .9, 1, 0), r2: R(8.6, C.b2, 1.3, .4, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, .2, 1, 0), dot: D(2.9, C.ink, 1), glow: .55, x: { waves: 1 }, lvl: 1 },
  thinking:     { r1: R(5.6, C.b1, 1.4, .8, 2.1, 2.6, -190), r2: R(8.6, C.b2, 1.4, .8, 3.6, 3.2, 313), r3: R(11.6, C.b3, 1.2, .3, 1, 0), dot: D(2.8, C.ink, 1, .55), glow: .12 },
  searching:    { r1: R(5.6, C.b1, 1.5, .9, 1, 0), r2: R(8.6, C.b2, 1.3, 0, 4.2, 4.2, 25.7), r3: R(11.6, C.white, 1.2, .12, 1, 0), dot: D(2.8, C.ink, 1), glow: .12, x: { comets: 1 } },
  working:      { r1: R(5.6, C.ink, 1.4, .16, 1, 0), r2: R(8.6, C.b2, 1.3, 0, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, 0, 1, 0), dot: D(2.8, C.ink, 1), glow: .12, x: { pulses: 1 } },
  speaking:     { r1: R(5.6, C.b1, 1.5, .9, 1, 0), r2: R(8.6, C.b2, 1.3, .4, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, .2, 1, 0), dot: D(2.9, C.ink, 1), glow: .18, x: { waves: 1 }, speak: 1 },
  waiting:      { r1: R(5.6, C.b1, 1.5, .9, 1, 0), r2: R(8.6, C.b2, 1.3, .5, 4.2, 4.2, 60), r3: R(11.6, C.b3, 1.2, .35, 2.5, 4), dot: D(2.8, C.ink, 1), glow: .12, bounce: 1, blink: 1 },
  error:        { r1: R(5.6, C.err, 1.5, .85, 1, 0), r2: R(8.6, C.err, 1.3, 0, 4.2, 4.2, 25.7), r3: R(11.6, C.err, 1.2, .25, 1, 0), dot: D(2.8, C.err, 1), glow: .1, breath: 1, blink: 1 },
  done:         { r1: R(6.4, C.ok, 1.5, .9, 1, 0), r2: R(6.4, C.ok, 1.3, 0, 4.2, 4.2, 25.7), r3: R(3.2, C.ok, 1.2, 0, 1, 0), dot: D(2.8, C.ink, 0), glow: 0, x: { tick: 1 } },
  transcribing: { r1: R(5.6, C.b1, 1.5, 0, 1, 0), r2: R(8.6, C.b2, 1.3, 0, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, 0, 1, 0), dot: D(2.5, C.ink, .85), glow: 0, x: { quad: 1 } },
  scn:          { r1: R(5.6, C.b1, 1.5, 0, 1, 0), r2: R(8.6, C.b2, 1.3, 0, 4.2, 4.2, 25.7), r3: R(11.6, C.b3, 1.2, 0, 1, 0), dot: D(2.8, C.ink, 1), glow: .12, x: { scn: 1 } },
});

const lerpC = (a, b, k) => [0, 1, 2].map((i) => Math.round(lerp(a[i], b[i], k)));
const strC = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
const ease = (fr) => (fr < .5 ? 2 * fr * fr : 1 - Math.pow(-2 * fr + 2, 2) / 2);

export function createNous({ size = 18, getLevel = null, zoom = null } = {}) {
  // На малых размерах геометрия 96x64 превращает ядро в субпиксель —
  // компенсируем зумом вокруг центра (кольца за краем обрезаются, читается ядро+кольца).
  const z = zoom || Math.min(4, Math.max(1, 96 / size));
  const tx = 48 * (1 - z), ty = 32 * (1 - z);
  const root = document.documentElement;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 96 64");
  svg.setAttribute("class", "nous-mark");
  svg.style.width = size + "px";
  svg.style.height = (size * 64 / 96) + "px";
  svg.style.display = "block";
  svg.innerHTML = `<g transform="translate(${tx.toFixed(2)} ${ty.toFixed(2)}) scale(${z.toFixed(2)})"><circle class="nr3" cx="48" cy="32" fill="none"/>
    <circle class="nr2" cx="48" cy="32" fill="none"/>
    <circle class="nr1" cx="48" cy="32" fill="none"/>
    <g class="nx" data-x="comets" style="opacity:0">
      <circle cx="48" cy="32" r="11.6" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="1"/>
      <circle class="ca" r="1.8"/><circle class="cb" r="1.3"/>
    </g>
    <g class="nx" data-x="waves" style="opacity:0">
      <circle class="xw" cx="48" cy="32" fill="none" stroke-width="1.7"/>
      <circle class="xw" cx="48" cy="32" fill="none" stroke-width="1.7"/>
      <circle class="xw" cx="48" cy="32" fill="none" stroke-width="1.7"/>
    </g>
    <g class="nx" data-x="pulses" style="opacity:0">
      <circle class="xp" cx="48" cy="32" fill="none" stroke-width="1.6"/>
      <circle class="xp" cx="48" cy="32" fill="none" stroke-width="1.6"/>
      <circle class="xp" cx="48" cy="32" fill="none" stroke-width="1.6"/>
    </g>
    <g class="nx" data-x="quad" style="opacity:0"><g class="xqr">
      <circle cx="48" cy="32" r="9.5" fill="none" stroke-width="2.4" stroke-dasharray="14.9 44.8"/>
      <circle cx="48" cy="32" r="9.5" fill="none" stroke-width="2.4" stroke-dasharray="14.9 44.8" transform="rotate(90 48 32)"/>
      <circle cx="48" cy="32" r="9.5" fill="none" stroke-width="2.4" stroke-dasharray="14.9 44.8" transform="rotate(180 48 32)"/>
      <circle cx="48" cy="32" r="9.5" fill="none" stroke-width="2.4" stroke-dasharray="14.9 44.8" transform="rotate(270 48 32)"/>
    </g></g>
    <g class="nx" data-x="scn" style="opacity:0">
      <path d="M48 20.4L56.2 23.8L59.6 32L56.2 40.2L48 43.6L39.8 40.2L36.4 32L39.8 23.8Z" fill="none" stroke="rgba(255,255,255,.12)" stroke-width="1"/>
      <g class="xnodes">${[...Array(8)].map(() => `<circle r="1.7"/>`).join("")}</g>
      <circle class="xrunner" r="2.3"/>
    </g>
    <path class="xtick" d="M45.4 32.2l1.9 2 3.5-3.9" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" style="opacity:0"/>
    <circle class="nglow" cx="48" cy="32" r="7" style="opacity:0"/>
    <g class="ncore"><circle class="ndot" r="2.8"/><circle class="nhi" r="2.8" style="opacity:.9"/></g></g>`;

  let C = readColors(root);
  let cur = JSON.parse(JSON.stringify(STATES(C).idle));
  cur.x = { comets: 0, waves: 0, pulses: 0, quad: 0, tick: 0, scn: 0 };
  let target = STATES(C).idle;
  let ang1 = 0, ang2 = 0, lvl = 0, lvlT = 0, env = 0, last = performance.now();
  const q = (s) => svg.querySelector(s);
  const els = { r1: q(".nr1"), r2: q(".nr2"), r3: q(".nr3"), dot: q(".ndot"), hi: q(".nhi"),
    core: q(".ncore"), glow: q(".nglow"), comets: q('[data-x="comets"]'), cA: q(".ca"), cB: q(".cb"),
    waves: q('[data-x="waves"]'), pulses: q('[data-x="pulses"]'), quad: q('[data-x="quad"]'),
    xqr: q(".xqr"), scn: q('[data-x="scn"]'), nodes: [...q(".xnodes").children],
    runner: q(".xrunner"), tick: q(".xtick"),
    xw: [...q('[data-x="waves"]').querySelectorAll(".xw")],
    xp: [...q('[data-x="pulses"]').querySelectorAll(".xp")] };
  const SCN = [...Array(8)].map((_, k) => {
    const a = (k * 45 - 90) * Math.PI / 180;
    return [48 + 11.6 * Math.cos(a), 32 + 11.6 * Math.sin(a)];
  });
  const glowFill = "url(#nousGlow)";
  // локальный radial-градиент свечения с уникальным id на экземпляр
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const gid = "nousGlow" + Math.random().toString(36).slice(2, 8);
  defs.innerHTML = `<radialGradient id="${gid}" cx=".5" cy=".5" r=".5">`
    + `<stop offset="0" stop-color="var(--accent)" stop-opacity=".85"/>`
    + `<stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></radialGradient>`;
  svg.prepend(defs);
  els.glow.setAttribute("fill", `url(#${gid})`);
  const strokes = { r1: els.r1, r2: els.r2, r3: els.r3 };
  for (const n of els.nodes) n.setAttribute("fill", "rgba(255,255,255,.18)");

  function set(state) {
    const s = STATES(C)[state] || STATES(C).idle;
    target = s;
  }
  function refreshColors() {
    C = readColors(root);
    target = Object.assign(STATES(C)[targetName] || STATES(C).idle, {});
    target = STATES(C)[targetName];
  }
  let targetName = "idle";

  let raf = null;
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const k = 1 - Math.exp(-dt * 7);
    for (const key of ["r1", "r2", "r3"]) {
      const c = cur[key], t = target[key];
      c.r = lerp(c.r, t.r, k); c.w = lerp(c.w, t.w, k); c.op = lerp(c.op, t.op, k);
      c.dA = lerp(c.dA, t.dA, k); c.dB = lerp(c.dB, t.dB, k); c.spd = lerp(c.spd, t.spd || 0, k);
      c.c = lerpC(c.c, t.c, k);
      const e = strokes[key];
      e.setAttribute("r", c.r.toFixed(2));
      e.setAttribute("stroke", strC(c.c));
      e.setAttribute("stroke-width", c.w.toFixed(2));
      e.setAttribute("stroke-dasharray", `${c.dA.toFixed(2)} ${c.dB.toFixed(2)}`);
      let op = c.op;
      if (key === "r3" && cur.blink > .01) op *= 1 - cur.blink * .35 * (.5 - .5 * Math.cos(now * .0163));
      e.setAttribute("opacity", op.toFixed(3));
    }
    ang1 += cur.r1.spd * dt; ang2 += cur.r2.spd * dt;
    els.r1.setAttribute("transform", `rotate(${ang1.toFixed(1)} 48 32)`);
    els.r2.setAttribute("transform", `rotate(${ang2.toFixed(1)} 48 32)`);
    for (const key of ["glow", "breath", "bounce", "lvl", "speak", "blink"]) {
      cur[key] = lerp(cur[key] || 0, target[key] || 0, k);
    }
    for (const key of ["comets", "waves", "pulses", "quad", "tick", "scn"]) {
      cur.x[key] = lerp(cur.x[key] || 0, (target.x || {})[key] || 0, k);
    }
    cur.dot.r = lerp(cur.dot.r, target.dot.r, k);
    cur.dot.op = lerp(cur.dot.op, target.dot.op, k);
    cur.dot.amp = lerp(cur.dot.amp || 0, target.dot.amp || 0, k);
    cur.dot.c = lerpC(cur.dot.c, target.dot.c, k);
    if (Math.random() < .1) lvlT = Math.random() * .25; else if (Math.random() < .2) lvlT = .45 + Math.random() * .55;
    lvl += (lvlT - lvl) * .3;
    env *= .9; if (Math.random() < .07) env = .5 + Math.random() * .5;
    const ext = getLevel ? getLevel() : 0;
    if (ext > 0) lvl = Math.max(lvl, Math.min(1, ext));
    const amp = Math.sin(now * .00546) * cur.dot.amp * .18;
    const breath = Math.sin(now * .0021) * .13 * cur.breath;
    const dotR = Math.max(0, cur.dot.r * (1 + cur.lvl * lvl * .4 + cur.speak * env * .5 + amp + breath));
    const dy = -1.2 * Math.abs(Math.sin((now % 1600) / 1600 * Math.PI)) * cur.bounce;
    els.core.setAttribute("transform", `translate(48 ${(32 + dy).toFixed(2)})`);
    els.dot.setAttribute("r", dotR.toFixed(2));
    els.hi.setAttribute("r", dotR.toFixed(2));
    els.dot.setAttribute("fill", strC(cur.dot.c));
    els.dot.setAttribute("opacity", cur.dot.op.toFixed(3));
    els.hi.setAttribute("opacity", (cur.dot.op * .9).toFixed(3));
    els.glow.setAttribute("r", (6 + cur.lvl * lvl * 3.5).toFixed(2));
    els.glow.setAttribute("opacity", (cur.glow * (.35 + cur.lvl * lvl * .65)).toFixed(3));
    const wOp = cur.x.waves || 0;
    els.waves.style.opacity = wOp.toFixed(3);
    if (wOp > .01) els.xw.forEach((n, i) => {
      const ph = ((now / 1800) + i / 3) % 1;
      n.setAttribute("r", (4.5 + ph * 7.3).toFixed(2));
      n.setAttribute("opacity", (.75 * (1 - ph)).toFixed(3));
      n.setAttribute("stroke", strC([C.b1, C.b2, C.b3][i]));
    });
    const pOp = cur.x.pulses || 0;
    els.pulses.style.opacity = pOp.toFixed(3);
    if (pOp > .01) els.xp.forEach((n, i) => {
      const ph = ((now / 1500) + i / 3) % 1;
      n.setAttribute("r", (3.5 + ph * 8).toFixed(2));
      n.setAttribute("opacity", (.8 * (1 - ph)).toFixed(3));
      n.setAttribute("stroke", strC([C.b1, C.b2, C.b3][i]));
    });
    const cOp = cur.x.comets || 0;
    els.comets.style.opacity = cOp.toFixed(3);
    if (cOp > .01) {
      const a = now * .00286;
      els.cA.setAttribute("cx", (48 + 11.6 * Math.cos(a)).toFixed(2));
      els.cA.setAttribute("cy", (32 + 11.6 * Math.sin(a)).toFixed(2));
      els.cB.setAttribute("cx", (48 + 11.6 * Math.cos(a + Math.PI)).toFixed(2));
      els.cB.setAttribute("cy", (32 + 11.6 * Math.sin(a + Math.PI)).toFixed(2));
      els.cA.setAttribute("fill", strC(C.b3)); els.cB.setAttribute("fill", strC(C.b2));
    }
    const qOp = cur.x.quad || 0;
    els.quad.style.opacity = qOp.toFixed(3);
    if (qOp > .01) els.xqr.querySelectorAll("circle").forEach((n, i) => {
      n.setAttribute("stroke", strC([C.b1, C.b2, C.b3, C.b4][i]));
    });
    els.xqr.setAttribute("transform", `rotate(${(now * .4) % 360} 48 32)`);
    els.tick.style.opacity = (cur.x.tick || 0).toFixed(3);
    els.tick.setAttribute("stroke", strC(C.ok));
    const sOp = cur.x.scn || 0;
    els.scn.style.opacity = sOp.toFixed(3);
    if (sOp > .01) {
      const prog = (now / 1000 * 1.6) % 8;
      const i = Math.floor(prog), fr = prog - i;
      const e = ease(fr);
      const p0 = SCN[i], p1 = SCN[(i + 1) % 8];
      els.runner.setAttribute("cx", lerp(p0[0], p1[0], e).toFixed(2));
      els.runner.setAttribute("cy", lerp(p0[1], p1[1], e).toFixed(2));
      els.runner.setAttribute("fill", strC(C.b3));
      els.nodes.forEach((n, j) => {
        const behind = (i - j + 8) % 8;
        if (behind === 0) {
          n.setAttribute("fill", strC(C.b1));
          n.setAttribute("r", (1.7 + Math.sin(now * .008) * .5).toFixed(2));
        } else if (behind < 5) {
          const f = 1 - behind / 5;
          n.setAttribute("fill", `rgba(${C.ok[0]},${C.ok[1]},${C.ok[2]},${(f * .95).toFixed(2)})`);
          n.setAttribute("r", "1.7");
        } else {
          n.setAttribute("fill", "rgba(255,255,255,.18)");
          n.setAttribute("r", "1.7");
        }
      });
    }
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);

  return {
    el: svg,
    set(state) { targetName = state; set(state); },
    refreshColors,
    destroy() { if (raf) cancelAnimationFrame(raf); svg.remove(); },
  };
}
