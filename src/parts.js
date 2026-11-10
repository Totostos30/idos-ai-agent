// Каталог настраиваемых частей островка. Общий для островка (режим «✎ Настроить») и окна настроек.
// Хранение: island.parts.<ключ> = false — элемент скрыт (по умолчанию всё включено),
// island.hidden_tabs / island.hidden_tools — списки скрытых вкладок панели и кнопок инструментов.

export const PARTS = [
  ["orb", "Персонаж / шарик", "Капсула"],
  ["clock", "Часы", "Капсула"],
  ["battery", "Батарея", "Капсула"],
  ["weather", "Погода", "Капсула"],
  ["privacy", "Микрофон и камера заняты", "Капсула"],
  ["caps", "Caps Lock", "Капсула"],
  ["layout", "Раскладка клавиатуры", "Капсула"],
  ["vpn", "VPN", "Капсула"],
  ["core_status", "Статус ядра", "Капсула"],
  ["volume_osd", "Индикатор громкости", "Капсула"],

  ["music_cover", "Обложка", "Музыка в капсуле"],
  ["music_title", "Название трека", "Музыка в капсуле"],
  ["music_progress", "Полоса прогресса", "Музыка в капсуле"],
  ["music_eq", "Эквалайзер", "Музыка в капсуле"],
  ["music_controls", "Кнопки ⏮ ⏯ ⏭", "Музыка в капсуле"],
  ["lyrics", "Строка песни", "Музыка в капсуле"],

  ["agent_steps", "Шаги агента", "Агент в капсуле"],
  ["agent_stop", "Кнопка «Стоп»", "Агент в капсуле"],
  ["reply_text", "Текст ответа", "Агент в капсуле"],
  ["mic_meter", "Уровень микрофона", "Агент в капсуле"],

  ["p_player", "Плеер", "Панель: «Сейчас»"],
  ["p_lyrics", "Текст песни", "Панель: «Сейчас»"],
  ["p_progress", "Прогресс и время трека", "Панель: «Сейчас»"],
  ["p_eq", "Эквалайзер", "Панель: «Сейчас»"],
  ["w_weather", "Виджет погоды", "Панель: «Сейчас»"],
  ["w_clock", "Виджет часов и даты", "Панель: «Сейчас»"],
  ["w_battery", "Виджет батареи", "Панель: «Сейчас»"],
  ["w_timers", "Таймеры", "Панель: «Сейчас»"],

  ["obs", "Управление OBS", "Панель: «Инструменты»"],

  ["pa_model", "Текущая модель", "Панель: «Агент»"],
  ["pa_steps", "Шаги", "Панель: «Агент»"],
  ["pa_reply", "Последний ответ", "Панель: «Агент»"],
  ["pa_input", "Поле задачи и микрофон", "Панель: «Агент»"],

  ["h_orb", "Шарик в заголовке", "Заголовок панели"],
  ["h_edit", "Кнопка ✎ «Настроить»", "Заголовок панели"],
  ["h_pin", "Кнопка «Закрепить»", "Заголовок панели"],
  ["h_open", "Кнопка «Окно Эйдоса»", "Заголовок панели"],
];

export const TAB_LIST = [["now", "Сейчас"], ["tools", "Инструменты"], ["clip", "Буфер"], ["agent", "Агент"]];

export const TOOL_LIST = [
  ["shot_area", "Область"], ["shot_screen", "Экран"], ["ocr", "Текст (OCR)"], ["qr", "QR-код"], ["color", "Пипетка"],
  ["record", "Запись экрана"], ["record_area", "Запись области"], ["dictation", "Диктовка"], ["note", "Заметка"], ["timer", "Таймер"],
];

// старые отдельные флажки остаются рабочими
const LEGACY = { battery: "show_battery", weather: "show_weather", lyrics: "lyrics" };

export function partOn(island, key) {
  const I = island || {};
  if (LEGACY[key] && I[LEGACY[key]] === false) return false;
  return !(I.parts && I.parts[key] === false);
}

export function listOn(island, kind, id) {
  const hidden = (island && island["hidden_" + kind]) || [];
  return !hidden.includes(id);
}

// значения для settings.update: показать/скрыть часть
export function partValues(key, show) {
  const v = { ["island.parts." + key]: show };
  if (LEGACY[key]) v["island." + LEGACY[key]] = show;
  return v;
}

export const RESET_VALUES = {
  "island.parts": {}, "island.hidden_tabs": [], "island.hidden_tools": [],
  "island.show_battery": true, "island.show_weather": true, "island.lyrics": true,
};

// применить изменение к локальной копии настроек (чтобы интерфейс обновился сразу)
export function applyValues(cfg, values) {
  for (const [path, val] of Object.entries(values)) {
    const parts = path.split(".");
    let node = cfg;
    for (const p of parts.slice(0, -1)) { if (!node[p] || typeof node[p] !== "object") node[p] = {}; node = node[p]; }
    node[parts[parts.length - 1]] = Array.isArray(val) ? val.slice() : val && typeof val === "object" ? Object.assign({}, val) : val;
  }
}
