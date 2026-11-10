// Эйдос (IDOS) — оболочка Tauri 2.
// Запускает ядро `python -m core`, держит прозрачный остров поверх окон (без фокуса, клики сквозь пустое место),
// главное окно, окно выделения области экрана, трей и глобальные горячие клавиши.
// Вся логика агента живёт в ядре; интерфейс говорит с ним напрямую по WebSocket (порт и токен — из data/runtime/core.json).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};

use serde_json::{json, Value};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// Начальный размер прозрачного окна острова; при размещении ширина подстраивается под экран.
/// Капсула «дышит» внутри него средствами CSS, а клики вне её формы уходят в окна под ними.
const ISLAND_W: f64 = 820.0;
const ISLAND_H: f64 = 660.0;

#[derive(Default)]
struct Shared {
    child: Option<Child>,
    spawned: Option<SystemTime>,
    core_error: String,
    hit: (f64, f64, f64, f64),
    focused: bool,
    island_hidden: bool,
    hotkeys: HashMap<u32, String>,
    select: Value,
    monitor: usize,
    offset_x: f64,
}

struct AppState(Mutex<Shared>);

fn with_state<R>(app: &AppHandle, f: impl FnOnce(&mut Shared) -> R) -> R {
    let state = app.state::<AppState>();
    let mut guard = state.0.lock().unwrap_or_else(|e| e.into_inner());
    f(&mut guard)
}

// ------------------------------------------------------------------ core process

fn root_dir() -> PathBuf {
    if let Ok(home) = std::env::var("IDOS_HOME") {
        if !home.trim().is_empty() {
            return PathBuf::from(home.trim());
        }
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        candidates.extend(exe.ancestors().skip(1).map(Path::to_path_buf));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.extend(cwd.ancestors().map(Path::to_path_buf));
    }
    for dir in &candidates {
        if dir.join("core").join("__main__.py").exists() {
            return dir.clone();
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn python_exe(root: &Path) -> PathBuf {
    let scripts = root.join(".venv").join("Scripts");
    for name in ["pythonw.exe", "python.exe"] {
        let candidate = scripts.join(name);
        if candidate.exists() {
            return candidate;
        }
    }
    PathBuf::from(if cfg!(windows) { "pythonw" } else { "python3" })
}

fn external_core() -> bool {
    matches!(std::env::var("IDOS_EXTERNAL_CORE").as_deref(), Ok("1") | Ok("true"))
}

fn spawn_core(app: &AppHandle) -> Result<(), String> {
    if external_core() {
        return Ok(());
    }
    let root = root_dir();
    let mut cmd = Command::new(python_exe(&root));
    cmd.args(["-m", "core"])
        .current_dir(&root)
        .env("IDOS_HOME", &root)
        .env("IDOS_PARENT_PID", std::process::id().to_string())
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    match cmd.spawn() {
        Ok(child) => {
            with_state(app, |s| {
                s.child = Some(child);
                s.spawned = Some(SystemTime::now());
                s.core_error.clear();
            });
            Ok(())
        }
        Err(e) => {
            let message = format!("Не удалось запустить ядро Эйдоса ({}): {e}. Запустите Setup.ps1.", root.display());
            with_state(app, |s| s.core_error = message.clone());
            Err(message)
        }
    }
}

fn stop_core(app: &AppHandle) {
    let child = with_state(app, |s| s.child.take());
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Порт и токен ядра. `ready: false`, пока ядро не записало свежий runtime-файл.
#[tauri::command]
fn core_info(app: AppHandle) -> Result<Value, String> {
    let root = root_dir();
    let file = root.join("data").join("runtime").join("core.json");
    let (spawned, error, exited) = with_state(&app, |s| {
        let exited = match s.child.as_mut().map(|c| c.try_wait()) {
            Some(Ok(Some(status))) => Some(status.to_string()),
            _ => None,
        };
        (s.spawned, s.core_error.clone(), exited)
    });
    let modified = std::fs::metadata(&file).ok().and_then(|m| m.modified().ok());
    let fresh = match (modified, spawned) {
        (Some(m), Some(started)) => m + Duration::from_secs(3) >= started,
        (Some(_), None) => true,
        _ => false,
    };
    if fresh && exited.is_none() {
        if let Ok(text) = std::fs::read_to_string(&file) {
            if let Ok(mut info) = serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) {
                info["ready"] = json!(true);
                info["root"] = json!(root.to_string_lossy());
                return Ok(info);
            }
        }
    }
    Ok(json!({"ready": false, "error": error, "exited": exited, "root": root.to_string_lossy()}))
}

#[tauri::command]
async fn restart_core(app: AppHandle) -> Result<(), String> {
    stop_core(&app);
    std::thread::sleep(Duration::from_millis(1200));
    spawn_core(&app)
}

// ------------------------------------------------------------------ Win32 helpers

#[cfg(windows)]
fn set_noactivate(window: &WebviewWindow, on: bool) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetWindowLongPtrW, SetWindowLongPtrW, GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
    };
    if let Ok(hwnd) = window.hwnd() {
        let h = hwnd.0 as windows_sys::Win32::Foundation::HWND;
        unsafe {
            let style = GetWindowLongPtrW(h, GWL_EXSTYLE);
            let tool = WS_EX_TOOLWINDOW as isize;
            let noact = WS_EX_NOACTIVATE as isize;
            let next = if on { style | tool | noact } else { (style & !noact) | tool };
            SetWindowLongPtrW(h, GWL_EXSTYLE, next);
        }
    }
}

#[cfg(not(windows))]
fn set_noactivate(_window: &WebviewWindow, _on: bool) {}

/// Скрыть окно от скриншотов и записи экрана (WDA_EXCLUDEFROMCAPTURE, Windows 10 2004+).
#[cfg(windows)]
fn set_capture_hidden(window: &WebviewWindow, hidden: bool) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE, WDA_NONE};
    if let Ok(hwnd) = window.hwnd() {
        let h = hwnd.0 as windows_sys::Win32::Foundation::HWND;
        unsafe {
            SetWindowDisplayAffinity(h, if hidden { WDA_EXCLUDEFROMCAPTURE } else { WDA_NONE });
        }
    }
}

#[cfg(not(windows))]
fn set_capture_hidden(_window: &WebviewWindow, _hidden: bool) {}

// ------------------------------------------------------------------ island window

fn island(app: &AppHandle) -> Result<WebviewWindow, String> {
    app.get_webview_window("island").ok_or_else(|| "Окно острова не создано".to_string())
}

fn place_island(app: &AppHandle) -> Result<(), String> {
    let window = island(app)?;
    let (index, offset) = with_state(app, |s| (s.monitor, s.offset_x));
    let monitors = window.available_monitors().map_err(|e| e.to_string())?;
    let monitor = match monitors.get(index) {
        Some(m) => m.clone(),
        None => window.primary_monitor().map_err(|e| e.to_string())?.ok_or("Не найден экран")?,
    };
    let scale = monitor.scale_factor();
    let width = monitor.size().width as f64 / scale;
    window.set_size(tauri::LogicalSize::new(width, ISLAND_H)).map_err(|e| e.to_string())?;
    let x = monitor.position().x as f64 + offset * scale;
    window
        .set_position(PhysicalPosition::new(x.round() as i32, monitor.position().y))
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn create_island(app: &AppHandle) -> Result<(), String> {
    let window = WebviewWindowBuilder::new(app, "island", WebviewUrl::App("index.html".into()))
        .title("Эйдос")
        .inner_size(ISLAND_W, ISLAND_H)
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .resizable(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .focused(false)
        .visible(false)
        .build()
        .map_err(|e| e.to_string())?;
    set_noactivate(&window, true);
    set_capture_hidden(&window, true);
    let _ = window.set_ignore_cursor_events(true);
    place_island(app)?;
    window.show().map_err(|e| e.to_string())?;
    Ok(())
}

/// Следим за курсором: над формой острова окно принимает мышь, вне её — пропускает клики насквозь.
fn hit_loop(app: AppHandle) {
    let mut previous: Option<bool> = None;
    let mut last_cursor = (f64::NAN, f64::NAN);
    let mut strip_hover_started: Option<Instant> = None;
    loop {
        std::thread::sleep(Duration::from_millis(30));
        let Ok(window) = island(&app) else { continue };
        let (hit, focused, hidden) = with_state(&app, |s| (s.hit, s.focused, s.island_hidden));
        let raw_inside = if hidden {
            false
        } else if focused {
            true
        } else {
            match (app.cursor_position(), window.outer_position(), window.scale_factor()) {
                (Ok(cursor), Ok(pos), Ok(scale)) => {
                    let x = (cursor.x - pos.x as f64) / scale;
                    let y = (cursor.y - pos.y as f64) / scale;
                    x >= hit.0 && y >= hit.1 && x <= hit.0 + hit.2 && y <= hit.1 + hit.3
                }
                _ => false,
            }
        };
        // The collapsed strip is a 12px hit region. Keep the transparent window
        // click-through until the cursor has stayed on that strip for 1 second;
        // otherwise Windows/WebView can deliver hover immediately on activation.
        let strip = !hidden && !focused && hit.2 > 0.0 && hit.3 <= 12.0;
        let inside = if strip && raw_inside {
            let started = strip_hover_started.get_or_insert_with(Instant::now);
            started.elapsed() >= Duration::from_secs(1)
        } else {
            strip_hover_started = None;
            raw_inside
        };
        // Координаты курсора нужны жидкому краю (тянется к курсору) и персонажам (глаза следят).
        if !hidden {
            if let (Ok(cursor), Ok(pos), Ok(scale)) = (app.cursor_position(), window.outer_position(), window.scale_factor()) {
                let x = (cursor.x - pos.x as f64) / scale;
                let y = (cursor.y - pos.y as f64) / scale;
                if (x - last_cursor.0).abs() > 1.0 || (y - last_cursor.1).abs() > 1.0 {
                    last_cursor = (x, y);
                    if y < 420.0 {
                        let _ = window.emit("island-cursor", (x, y));
                    }
                }
            }
        }
        if previous != Some(inside) {
            let _ = window.set_ignore_cursor_events(!inside);
            let _ = window.emit("island-hover", inside);
            previous = Some(inside);
        }
    }
}

#[tauri::command]
fn set_hit_region(app: AppHandle, x: f64, y: f64, width: f64, height: f64) {
    with_state(&app, |s| s.hit = (x, y, width, height));
}

/// Временно разрешить острову фокус клавиатуры (командная строка, быстрый вопрос). После — снова «без фокуса».
#[tauri::command]
fn island_focus(app: AppHandle, focus: bool) -> Result<(), String> {
    let window = island(&app)?;
    with_state(&app, |s| s.focused = focus);
    set_noactivate(&window, !focus);
    if focus {
        let _ = window.set_ignore_cursor_events(false);
        window.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn island_place(app: AppHandle, monitor: usize, offset_x: f64) -> Result<(), String> {
    with_state(&app, |s| {
        s.monitor = monitor;
        s.offset_x = offset_x;
    });
    place_island(&app)
}

#[tauri::command]
fn island_visible(app: AppHandle, visible: bool) -> Result<(), String> {
    let window = island(&app)?;
    with_state(&app, |s| s.island_hidden = !visible);
    if visible {
        window.show().map_err(|e| e.to_string())
    } else {
        window.hide().map_err(|e| e.to_string())
    }
}

#[tauri::command]
fn set_capture_visible(app: AppHandle, visible: bool) -> Result<(), String> {
    set_capture_hidden(&island(&app)?, !visible);
    Ok(())
}

#[tauri::command]
fn monitors(app: AppHandle) -> Result<Value, String> {
    let window = island(&app)?;
    let list: Vec<Value> = window
        .available_monitors()
        .map_err(|e| e.to_string())?
        .iter()
        .enumerate()
        .map(|(i, m)| {
            json!({"index": i, "name": m.name().cloned().unwrap_or_else(|| format!("Экран {}", i + 1)),
                   "x": m.position().x, "y": m.position().y, "width": m.size().width, "height": m.size().height,
                   "scale": m.scale_factor()})
        })
        .collect();
    Ok(json!(list))
}

// ------------------------------------------------------------------ main window

fn create_main(app: &AppHandle) -> Result<(), String> {
    let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Эйдос")
        .inner_size(1120.0, 760.0)
        .min_inner_size(860.0, 560.0)
        .decorations(false)
        .transparent(true)
        .center()
        .visible(false)
        .build()
        .map_err(|e| e.to_string())?;
    let handle = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = handle.hide();
        }
    });
    Ok(())
}

fn show_main_window(app: &AppHandle, tab: Option<String>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        if let Some(tab) = tab {
            let _ = window.emit("main-tab", tab);
        }
    }
}

#[tauri::command]
fn show_main(app: AppHandle, tab: Option<String>) {
    show_main_window(&app, tab);
}

// ------------------------------------------------------------------ area selector

#[tauri::command]
async fn open_selector(app: AppHandle, purpose: String) -> Result<(), String> {
    let base = island(&app)?;
    let cursor = app.cursor_position().map_err(|e| e.to_string())?;
    let monitors = base.available_monitors().map_err(|e| e.to_string())?;
    let found = monitors.into_iter().find(|m| {
        let p = m.position();
        let s = m.size();
        cursor.x >= p.x as f64
            && cursor.y >= p.y as f64
            && cursor.x < (p.x + s.width as i32) as f64
            && cursor.y < (p.y + s.height as i32) as f64
    });
    let monitor = match found {
        Some(m) => m,
        None => base.primary_monitor().map_err(|e| e.to_string())?.ok_or("Не найден экран")?,
    };
    let pos = *monitor.position();
    let size = *monitor.size();
    with_state(&app, |s| {
        s.select = json!({"purpose": purpose, "x": pos.x, "y": pos.y, "width": size.width, "height": size.height,
                          "scale": monitor.scale_factor()});
    });
    let window = match app.get_webview_window("select") {
        Some(w) => w,
        None => {
            let w = WebviewWindowBuilder::new(&app, "select", WebviewUrl::App("index.html".into()))
                .title("Эйдос — выделение")
                .transparent(true)
                .decorations(false)
                .shadow(false)
                .resizable(false)
                .always_on_top(true)
                .skip_taskbar(true)
                .visible(false)
                .build()
                .map_err(|e| e.to_string())?;
            set_capture_hidden(&w, true);
            w
        }
    };
    window.set_position(pos).map_err(|e| e.to_string())?;
    window.set_size(size).map_err(|e| e.to_string())?;
    let _ = window.emit("select-start", with_state(&app, |s| s.select.clone()));
    window.show().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn select_info(app: AppHandle) -> Value {
    with_state(&app, |s| s.select.clone())
}

#[tauri::command]
fn close_selector(app: AppHandle) {
    if let Some(window) = app.get_webview_window("select") {
        let _ = window.hide();
    }
}

// ------------------------------------------------------------------ hotkeys & tray

/// Перерегистрировать глобальные клавиши из настроек (имя → сочетание, например "command_bar" → "Alt+Space").
#[tauri::command]
fn register_hotkeys(app: AppHandle, map: HashMap<String, String>) -> Value {
    let shortcuts = app.global_shortcut();
    let _ = shortcuts.unregister_all();
    let mut ids: HashMap<u32, String> = HashMap::new();
    let mut errors = serde_json::Map::new();
    for (name, combo) in map {
        let combo = combo.trim().to_string();
        if combo.is_empty() {
            continue;
        }
        match combo.parse::<Shortcut>() {
            Ok(shortcut) => {
                let id = shortcut.id();
                match shortcuts.register(shortcut) {
                    Ok(()) => {
                        ids.insert(id, name);
                    }
                    Err(e) => {
                        errors.insert(name, json!(format!("{combo}: занято другой программой ({e})")));
                    }
                }
            }
            Err(e) => {
                errors.insert(name, json!(format!("{combo}: непонятное сочетание ({e})")));
            }
        }
    }
    let count = ids.len();
    with_state(&app, |s| s.hotkeys = ids);
    json!({"registered": count, "errors": errors})
}

fn on_hotkey(app: &AppHandle, name: &str) {
    if name == "chat" {
        show_main_window(app, Some("chat".into()));
    }
    let _ = app.emit("hotkey", name.to_string());
}

#[tauri::command]
fn set_tray_status(app: AppHandle, label: String) -> Result<(), String> {
    let tray = app.tray_by_id("idos-tray").ok_or("Значок в трее недоступен")?;
    let text = if label.trim().is_empty() { "Эйдос".to_string() } else { format!("Эйдос — {}", label.trim()) };
    tray.set_tooltip(Some(text)).map_err(|e| e.to_string())
}

#[tauri::command]
fn quit_app(app: AppHandle) {
    stop_core(&app);
    app.exit(0);
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Открыть Эйдос", true, None::<&str>)?;
    let toggle = MenuItem::with_id(app, "island", "Показать / скрыть остров", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Экстренная остановка агента", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Перезапустить ядро", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Настройки", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &toggle, &stop, &restart, &settings, &separator, &quit])?;
    let mut builder = TrayIconBuilder::with_id("idos-tray").tooltip("Эйдос").menu(&menu);
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show_main_window(app, None),
            "settings" => show_main_window(app, Some("settings".into())),
            "island" => {
                let hidden = with_state(app, |s| s.island_hidden);
                let _ = island_visible(app.clone(), hidden);
            }
            "stop" => {
                let _ = app.emit("emergency-stop", ());
            }
            "restart" => {
                let handle = app.clone();
                std::thread::spawn(move || {
                    stop_core(&handle);
                    std::thread::sleep(Duration::from_millis(1200));
                    let _ = spawn_core(&handle);
                    let _ = handle.emit("core-restarted", ());
                });
            }
            "quit" => {
                stop_core(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } = event {
                show_main_window(tray.app_handle(), None);
            }
            if let TrayIconEvent::Click { button: MouseButton::Middle, button_state: MouseButtonState::Up, .. } = event {
                let _ = tray.app_handle().emit("hotkey", "voice".to_string());
            }
        })
        .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app, None);
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let name = with_state(app, |s| s.hotkeys.get(&shortcut.id()).cloned());
                    if let Some(name) = name {
                        on_hotkey(app, &name);
                    }
                })
                .build(),
        )
        .manage(AppState(Mutex::new(Shared::default())))
        .invoke_handler(tauri::generate_handler![
            core_info,
            restart_core,
            set_hit_region,
            island_focus,
            island_place,
            island_visible,
            set_capture_visible,
            monitors,
            show_main,
            open_selector,
            select_info,
            close_selector,
            register_hotkeys,
            set_tray_status,
            quit_app
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            if let Err(e) = spawn_core(&handle) {
                eprintln!("{e}");
            }
            create_island(&handle).map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            create_main(&handle).map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            build_tray(&handle)?;
            let looper = handle.clone();
            std::thread::spawn(move || hit_loop(looper));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("не удалось запустить Эйдос")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                stop_core(app);
            }
        });
}
