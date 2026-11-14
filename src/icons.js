// Monoline SVG-иконки (гайдбук §3): сетка 24, stroke 1.7, currentColor, без эмодзи.
// Иконка возвращает строку SVG — вставляется через el()/innerHTML, где нужен Node — iconNode().
const P = {
  refresh: '<path d="M19 10a7 7 0 0 0-12-4L4 9m0-5v5h5M5 14a7 7 0 0 0 12 4l3-3m0 5v-5h-5"/>',
  pin: '<path d="M8 4.5h8M9 4.5v5l-3 4v1h12v-1l-3-4v-5M12 14.5v6"/>',
  open: '<path d="M13 4.5h6.5V11M19.5 4.5 10 14M9 5H5.5A1.5 1.5 0 0 0 4 6.5v12A1.5 1.5 0 0 0 5.5 20h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',

  chat: '<path d="M6.6 5h10.8A1.6 1.6 0 0 1 19 6.6v7.8a1.6 1.6 0 0 1-1.6 1.6h-7L6 19.5V16h-.4A1.6 1.6 0 0 1 4 14.4V6.6A1.6 1.6 0 0 1 5.6 5Z"/>',
  memory: '<path d="M12 6.6C10.4 5.1 8 4.6 5.6 4.9a1 1 0 0 0-.9 1v10.3a1 1 0 0 0 1.1 1c2.3-.2 4.5.2 6.2 1.6 1.7-1.4 3.9-1.8 6.2-1.6a1 1 0 0 0 1.1-1V5.9a1 1 0 0 0-.9-1C16 4.6 13.6 5.1 12 6.6Zm0 0v12.3"/>',
  key: '<circle cx="8" cy="8" r="3.6"/><path d="M10.6 10.6 19.4 19.4M16.4 16.4l2.2-2.2M13.4 13.4l2.2-2.2"/>',
  tools: '<path d="M5 7.2h14M5 12h14M5 16.8h14"/><circle cx="9.4" cy="7.2" r="1.9"/><circle cx="15" cy="12" r="1.9"/><circle cx="8.2" cy="16.8" r="1.9"/>',
  scenario: '<circle cx="6.2" cy="6.2" r="2.3"/><circle cx="17.8" cy="6.2" r="2.3"/><circle cx="12" cy="17.8" r="2.3"/><path d="M7.6 7.9 11 15.6M16.4 7.9 13 15.6"/>',
  settings: '<circle cx="12" cy="12" r="3.1"/><path d="M12 4.2v2.6M12 17.2v2.6M4.2 12h2.6M17.2 12h2.6M6.5 6.5l1.8 1.8M15.7 15.7l1.8 1.8M17.5 6.5l-1.8 1.8M8.3 15.7l-1.8 1.8"/>',
  plus: '<path d="M12 6v12M6 12h12"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  edit: '<path d="M4.5 19.5l4.3-.9 9.7-9.7a1.9 1.9 0 0 0-2.7-2.7l-9.7 9.7-.9 4.3Z"/><path d="M13.6 8.4l2.7 2.7"/>',
  minus: '<path d="M6 12h12"/>',
  expand: '<path d="M4 9.5V4h5.5M20 9.5V4h-5.5M4 14.5V20h5.5M20 14.5V20h-5.5"/>',
  stop: '<path d="M8.2 3.5h7.6l4.7 4.7v7.6l-4.7 4.7H8.2L3.5 15.8V8.2L8.2 3.5Z"/><path d="M12 7.6v4.6"/><circle cx="12" cy="15.6" r=".7" fill="currentColor" stroke="none"/>',
  end: '<rect x="3" y="7.5" width="18" height="9" rx="4.5"/><rect x="9.4" y="9.4" width="5.2" height="5.2" rx="1.2" fill="currentColor" stroke="none"/>',
  check: '<path d="M5 12.6l4.4 4.4L19 7.2"/>',
  alert: '<path d="M12 4.5 21 19.5H3Z"/><path d="M12 10.2v4"/><circle cx="12" cy="16.6" r=".4" fill="currentColor"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M15.6 15.6 20 20"/>',
  spinner: '<circle cx="12" cy="12" r="8" stroke-dasharray="38 12"/>',
  mic: '<rect x="9.2" y="4" width="5.6" height="10.5" rx="2.8"/><path d="M6.2 11.2a5.8 5.8 0 0 0 11.6 0M12 17v3"/>',
  spark: '<path d="M12 4.6l1.6 5.8 5.8 1.6-5.8 1.6L12 19.4l-1.6-5.8L4.6 12l5.8-1.6Z"/>',
  clock: '<circle cx="12" cy="12" r="7.5"/><path d="M12 8v4.4l3 1.8"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3.5V6M12 18v2.5M3.5 12H6M18 12h2.5"/>',
  moon: '<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10Z"/>',
  auto: '<circle cx="12" cy="12" r="7.5"/><path d="M12 4.5a7.5 7.5 0 0 0 0 15c-4 0-5.5-3.4-5.5-7.5S8 4.5 12 4.5Z" fill="currentColor" stroke="none"/>',
  palette: '<path d="M12 4.5a7.5 7.5 0 1 0 0 15c1.3 0 1.8-.9 1.8-1.7 0-1.9 2.1-1.3 3.9-1.3 1.3 0 1.8-1 1.8-2A7.5 7.5 0 0 0 12 4.5Z"/><circle cx="8" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="12" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="16" cy="10" r="1" fill="currentColor" stroke="none"/>',
  prev: '<path d="M17.5 5.5v13L8.6 12l8.9-6.5Z"/><path d="M6.5 5.5v13"/>',
  next: '<path d="M6.5 5.5v13L15.4 12l-8.9-6.5Z"/><path d="M17.5 5.5v13"/>',
  play: '<path d="M8 5.5v13l10-6.5-10-6.5Z"/>',
  pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
  home: '<path d="M4.5 11 12 4.5 19.5 11M6.5 9.5V19h11V9.5"/>',
  pip: '<circle cx="12" cy="12" r="7.5"/><circle cx="12" cy="12" r="2.4" fill="currentColor" stroke="none"/>',
  keyboard: '<rect x="4" y="7" width="16" height="10.5" rx="1.8"/><path d="M7.5 10.4h.01M10.5 10.4h.01M13.5 10.4h.01M16.5 10.4h.01M7.5 13.6h.01M10.5 13.6h.01M13.5 13.6h.01M16.5 13.6h.01M8 16.4h8" fill="none"/>',
  rec: '<circle cx="12" cy="12" r="5.5" fill="currentColor" stroke="none"/>',
  file: '<path d="M7 4h7l4 4v11a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19V5.5A1.5 1.5 0 0 1 7 4Z"/><path d="M13.5 4.2V8.5H18"/>',
  note: '<path d="M5.5 5.5h13v13h-13Z"/><path d="M8.5 9.5h7M8.5 12.5h7M8.5 15.5h4.5"/>',
  music: '<path d="M9 17.5V6.8a.8.8 0 0 1 .6-.8l7-1.7a.8.8 0 0 1 1 .8v10.4"/><circle cx="7" cy="17.5" r="2.2"/><circle cx="15.6" cy="15.5" r="2.2"/>',
  clip: '<path d="M8.5 12 15 5.8a3.4 3.4 0 0 1 4.8 4.8l-7.7 7.7a5.3 5.3 0 0 1-7.5-7.5l7.6-7.6"/>',
  shot: '<path d="M4 8.5V6a2 2 0 0 1 2-2h2.5M15.5 4H18a2 2 0 0 1 2 2v2.5M20 15.5V18a2 2 0 0 1-2 2h-2.5M8.5 20H6a2 2 0 0 1-2-2v-2.5"/><circle cx="12" cy="12" r="3.4"/>',
  download: '<path d="M12 4.5v9.5m0 0 3.8-3.8M12 14l-3.8-3.8M5 18.5h14"/>',
  history: '<path d="M4.6 12a7.4 7.4 0 1 1 2.2 5.2M4.6 12V7.6M4.6 12h4.4"/><path d="M12 8.6V12l2.6 1.6"/>',
  globe: '<circle cx="12" cy="12" r="7.5"/><path d="M4.5 12h15M12 4.5c2.2 2.1 3.4 4.7 3.4 7.5s-1.2 5.4-3.4 7.5c-2.2-2.1-3.4-4.7-3.4-7.5S9.8 6.6 12 4.5Z"/>',
  app: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M5 12h14M12 5v14"/>',
  calc: '<rect x="6" y="4.5" width="12" height="15" rx="2"/><path d="M9 8.5h6M9 12.5h2M12.8 12.5h2M9 15.7h2M12.8 15.7h2"/>',
  convert: '<path d="M4.5 8h13l-3-3M19.5 16h-13l3 3"/>',
  bell: '<path d="M12 4.5a5 5 0 0 1 5 5v3.6l1.6 2.6H5.4L7 13.1V9.5a5 5 0 0 1 5-5Z"/><path d="M10.2 18.6a2 2 0 0 0 3.6 0"/>',
  system: '<circle cx="12" cy="12" r="7.5"/><path d="M12 4.5V11"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v4.4"/><circle cx="12" cy="8" r=".6" fill="currentColor" stroke="none"/>',
  // — новые (островок и инструменты): щит, кадр, экран, распознавание, QR, батарея, камера, звук, сеть —
  shield: '<path d="M12 3.5 19 6.2v5.6c0 4.4-3 7.1-7 8.7-4-1.6-7-4.3-7-8.7V6.2L12 3.5Z"/><path d="M9.3 11.7l1.9 1.9 3.5-3.5"/>',
  crop: '<path d="M6.5 2.5v13a2 2 0 0 0 2 2h13"/><path d="M2.5 6.5h13a2 2 0 0 1 2 2v13"/>',
  monitor: '<rect x="3" y="4.5" width="18" height="11.5" rx="2"/><path d="M8.5 20h7M12 16v4"/>',
  ocr: '<path d="M4 7V5a1 1 0 0 1 1-1h2M17 4h2a1 1 0 0 1 1 1v2M20 17v2a1 1 0 0 1-1 1h-2M7 20H5a1 1 0 0 1-1-1v-2"/><path d="M8.5 8.5h7M12 8.5V15"/>',
  qr: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><path d="M14 14h2.5v2.5M20 14v.01M20 17v.01M20 20v.01M14 20v.01M17 17v.01"/>',
  battery: '<rect x="2.5" y="8" width="16" height="8.5" rx="2"/><path d="M21.5 11v2.5M6 11v2.5M9.5 11v2.5"/>',
  cam: '<path d="M3 8.5A1.5 1.5 0 0 1 4.5 7h2.6L8.7 5h6.6l1.6 2h2.6A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z"/><circle cx="12" cy="12.8" r="3.2"/>',
  volume: '<path d="M11 5.2 6.6 8.9H3.6v6.2h3L11 18.8V5.2Z"/><path d="M14.8 9.6a3.5 3.5 0 0 1 0 4.8M17.6 7.2a7 7 0 0 1 0 9.6"/>',
  wifi: '<path d="M3.5 9.6a12.6 12.6 0 0 1 17 0"/><path d="M6.6 13a8.6 8.6 0 0 1 10.8 0"/><path d="M9.6 16.3a4.6 4.6 0 0 1 4.8 0"/><circle cx="12" cy="19.2" r=".9" fill="currentColor" stroke="none"/>',
  // — виджеты островка: секундомер, песочные часы, кольцо отсчёта, датчик, чип, календарь, доллар, микшер, глаз —
  timer: '<circle cx="12" cy="13.5" r="7"/><path d="M10 3.2h4M12 3.2v3.3M12 13.5l2.8-2.8"/>',
  hourglass: '<path d="M7 3.5h10v2.6l-3.6 4v1.8l3.6 4v4.1H7v-4.1l3.6-4v-1.8L7 6.1Z"/>',
  ring: '<circle cx="12" cy="12" r="8" opacity=".3"/><path d="M12 4a8 8 0 0 1 8 8"/><path d="M12 12V8M12 12l3 2"/>',
  gauge: '<path d="M4.5 14a7.5 7.5 0 0 1 15 0"/><path d="M12 14l3.5-4.5"/><path d="M4.5 14h3M16.5 14h3M12 14h.01"/>',
  chipic: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 7V4.5M14 7V4.5M10 19.5V17M14 19.5V17M7 10H4.5M7 14H4.5M19.5 10H17M19.5 14H17"/><rect x="10" y="10" width="4" height="4" rx="1" fill="currentColor" stroke="none"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2.5"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4"/><circle cx="9" cy="14" r="1.1" fill="currentColor" stroke="none"/><circle cx="13" cy="14" r="1.1" fill="currentColor" stroke="none"/>',
  dollar: '<path d="M12 3.5v17"/><path d="M15.7 7.1c0-1.7-1.7-2.8-3.7-2.8s-3.7 1-3.7 2.7c0 4.1 7.4 2.3 7.4 6.5 0 1.8-1.7 2.9-3.7 2.9s-3.7-1.1-3.7-2.9"/>',
  mixer: '<path d="M6 17V9M12 17V5.5M18 17v-4"/><path d="M3.5 19.5h17"/><circle cx="6" cy="9" r="1.8" fill="currentColor" stroke="none"/><circle cx="12" cy="5.5" r="1.8" fill="currentColor" stroke="none"/><circle cx="18" cy="13" r="1.8" fill="currentColor" stroke="none"/>',
  eye: '<path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
};

export function icon(name, size = 16) {
  const body = P[name] || P.spark;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" `
    + `stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export function iconNode(name, size = 16) {
  const wrap = document.createElement("span");
  wrap.style.display = "inline-flex";
  wrap.style.verticalAlign = "-3px";
  wrap.innerHTML = icon(name, size);
  return wrap.firstChild ? wrap : document.createTextNode("");
}
