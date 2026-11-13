# План поддержки тем, включая пользовательские

Ветка: `re-design-ui` · Дата: 2026-11-13

## 1. Целевая архитектура: три слоя токенов

```
Прimitive (сырьё)      ──►  Semantic (смысл)      ──►  Компоненты
#7c8cff, 16px, 12px         --surface-1, --text-2      background: var(--surface-1)
                            цвет = ссылка на primitive  только semantic-токены
```

**Слой 1 — Primitive** (`src/tokens.css`, единственный файл с «сырыми» значениями):
бренд-палитра (`--brand-1..4` = 7c8cff/b46cff/4fd1ff/e07cff), нейтральная шкала, шкалы радиусов (5 значений), отступов (сетка 4px, 8 значений), типографики (6 размеров), движений (2 кривые + 3 длительности). Темы этот слой **не** трогают.

**Слой 2 — Semantic** (`:root[data-theme=…]`): смысловые поверхности и тексты. Тема = переопределение **только этого слоя**:

```css
:root, :root[data-theme="dark"] {
  --surface-0: #000;            /* фон окна */
  --surface-1: #111114;         /* карточка */
  --surface-2: #141417;         /* композер, bubble */
  --surface-3: #1b1b20;         /* toast, options */
  --text-1: #f5f5f7;            --text-2: var(--mut);
  --border-1: rgba(255,255,255,.09);
  --fill-1: rgba(255,255,255,.07); --fill-2: rgba(255,255,255,.13);
  --accent: #7c8cff;            --accent-contrast: #000;
  --ok/--warn/--err + фоны состояний;
}
:root[data-theme="light"] { /* тот же список, светлые значения */ }
```

**Слой 3 — Компоненты**: правила запрещают сырые hex; enforcement — grep-линт в CI-скрипте `scripts/design-lint.mjs` (уже есть у скилла design) + карта миграции соответствий:

| Старый hex | Токен |
|---|---|
| #000 / #0b0b0d / #0a0f16 | `--surface-0` |
| #111114 / #15151b | `--surface-1` |
| #141417 / #16181d | `--surface-2` |
| #1b1b20 | `--surface-3` |
| #f4f4f6 / #f7f7f9 / #fbfbfd / #f3f3f6 | `--surface-*` light-набора |
| #7c8cff (где угодно) | `--accent` |
| #b46cff / #4fd1ff / #e07cff | `--brand-2/3/4` |
| #35d07f / #ffb547 / #ff5a5f | `--ok / --warn / --err` |
| rgba(124,140,255,X) | `color-mix(in srgb, var(--accent) X%, transparent)` |

Светлые патчи `:root[data-theme="light"] .box {...}` и т.п. **удаляются** — покрываются token-блоком.

## 2. Встроенные темы

- **dark** — текущая (эталон)
- **light** — полный набор из токенов (не патчи), с проверкой контраста всех пар текст/поверхность ≥4.5:1 (текст ≥11px — ≥4.5, крупный ≥3)
- **system** — модуль слушает `matchMedia('(prefers-color-scheme: dark)')` + событие Tauri `theme-changed`, ставит `data-theme` фактической темы; `data-scheme="system"` хранится отдельно, чтобы переключение в настройках не терялось
- **Акценты независимо от темы**: `--accent`/`--accent-contrast` — отдельная настройка (пресеты: индиго #7c8cff, циан #4fd1ff, фиолет #b46cff, зелёный #35d07f, оранж #ffb547 + свой hex). Бренд-градиент логотипа/орба при смене акцента сохраняет 4 фирменных цвета (идентичность), но edge-glow/порты/скролл-обводка следуют акценту.

## 3. Пользовательские (кастомные) темы

**Формат файла** `data/themes/<slug>.json` (data/ вне Git, как и остальные пользовательские данные):

```json
{
  "name": "Моя тема",
  "base": "dark",
  "vars": {
    "--surface-0": "#0d1117",
    "--surface-1": "#161b22",
    "--accent": "#58a6ff",
    "--accent-contrast": "#000",
    "--ok": "#3fb950"
  }
}
```

Правила движка:
1. Кастом **глубоко сливается** поверх выбранного базового пресета: не указанные переменные наследуются от dark/light → нельзя получить «половину темы».
2. Редактируемый набор = только semantic-слой (~18 переменных). Primitive недоступны — это защищает шкалы и бренд.
3. **Контраст-гейт**: перед сохранением движок считает контраст пар (`--text-1/--surface-0..3`, `--text-2/--surface-*`, `--accent-contrast/--accent`) и не сохраняет тему с парой <4.5:1, показывая, какая пара провалена и её текущее значение.
4. Подпись темы попадает в карточку настроек и в `title` селекта.

## 4. Применение и синхронизация

**Новый модуль `src/theme.js` — единственная точка применения темы** (сейчас: только `island.js:721`, главное окно вообще не применяет):

```js
export async function applyTheme() {
  const cfg = await core.try("settings.get");
  const preset = cfg.theme?.preset || "dark";       // dark | light | system | <slug>
  const accent = cfg.theme?.accent;                  // hex | null
  const custom = preset не встроенный ? await loadTheme(preset) : null;
  const scheme = preset === "system" ? await systemScheme() : (custom?.base || preset);
  root.dataset.theme = scheme;                       // только dark | light (для color-scheme)
  root.dataset.scheme = preset;                      // что выбрал пользователь
  for (const [k, v] of Object.entries(custom?.vars || {})) root.style.setProperty(k, v);
  if (accent) root.style.setProperty("--accent", accent);
}
```

- Вызывается на старте в **каждом** окне (island, main, select — mount-функции уже единые через `main.js`).
- Синхронизация живьём: подписка `core.on("settings.changed", key.startsWith("theme"))` → `applyTheme()`. Шина уже есть (`island.js:846`) — оба окна обновятся без перезапуска.
- `core/config.py`: `"theme": {"preset": "dark", "accent": null}` (миграция старой строки `theme` в `preset` при первом чтении).
- Для `system` — `matchMedia` listener + Tauri window theme event; отписка при повторном apply (иначе утечка листенеров).

## 5. UI в настройках

Новая секция «Внешний вид» (вкладка Настройки, перед «Голос Эйдоса»):

- **Тема**: сегмент-контрол `Системная · Тёмная · Светлая` + список кастомных тем под ним
- **Акцент**: ряд цветных кружков-пресетов + поле `#hex` с валидацией
- **Кастомные темы**: «Создать из текущей» → редактор: имя, база (dark/light), поля 18 semantic-переменных с color-picker и мини-превью (карточка + кнопка + поле, те же компоненты), контраст-гейт у каждой пары, «Сохранить»/«Удалить»
- Превью применяется к окну настроек сразу (live preview через тот же applyTheme на виртуальном overlay — откат при «Отмене»)

## 6. Этапы внедрения (по коммиту на этап)

1. `src/tokens.css`: primitive + semantic dark/light; подключение в index.html перед остальными
2. Миграция app.css/island.css/select.css на токены по карте из §1; удаление светлопатчей; `theme.js` + правка `island.js:721` (убрать дублирующее присвоение) и `index.html` (убрать жёсткий `data-theme="dark"`, ставить из JS до первой отрисовки — иначе вспышка тёмной при светлой теме)
3. «Внешний вид» в настройках + system + акценты
4. Кастомные темы: RPC `themes.list/save/delete` в ядре (чтение/запись data/themes/), редактор с контраст-гейтом
5. commit-tree: подключить те же `--accent`/`--surface-0` через локальный mirror-скрипт (он живёт отдельно) — или оставить фирменную палитру, пометив как standalone (решить при проходе 8)
6. Проверка: оба окна + select + обе темы + system-переключение + сохранение/загрузка кастома; `npm run build`; линт на отсутствие hex вне tokens.css

## 7. Риски

- **FOUC темы**: index.html ставит `data-theme="dark"` статично; решается инлайном `<script>` в head, читающим settings из localStorage-кэша до загрузки модулей
- **Tauri-окна**: три независимых WebView — каждый применяет тему сам (уже покрыто §4)
- **color-mix поддержка**: WebView2 (Chromium 111+) поддерживает — ок для Tauri; для старых fallback не планируем
- **Не трогаем** `--brand-1..4` ни в одной теме — идентичность продукта
