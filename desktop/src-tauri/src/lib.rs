use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use chrono::DateTime;
use ed25519_dalek::{Signature, VerifyingKey};
use rand::{distr::Alphanumeric, Rng};
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use tauri_plugin_updater::{Update, UpdaterExt};

struct SidecarProcess {
    child: CommandChild,
    port: u16,
    secret: String,
}

struct SidecarState(Mutex<Option<SidecarProcess>>);

struct LifecycleState {
    explicit_exit: AtomicBool,
    confirmation_open: AtomicBool,
}

struct UpdaterState {
    busy: AtomicBool,
}

#[derive(Default)]
struct TrayStatus {
    enabled: bool,
    paused: bool,
    next_run_at: String,
}

const LEASE_MESSAGE_PREFIX: &str = "SEARCHCAR-LICENSE-LEASE-V1\n";
const UPDATE_CHECK_INTERVAL: Duration = Duration::from_secs(6 * 60 * 60);

/// Defense in depth for production builds.  The Python sidecar enforces the
/// same signed lease after startup; the native shell verifies it before it
/// starts any local HTTP server or browser process.
fn verify_required_lease(config_path: &Path, data_dir: &Path) -> Result<(), String> {
    let config: Value = read_json_value(config_path, "license configuration")?;
    if !config
        .get("enforcement")
        .and_then(Value::as_str)
        .unwrap_or("disabled")
        .eq_ignore_ascii_case("required")
    {
        return Ok(());
    }

    let public_keys = config
        .get("public_keys")
        .and_then(Value::as_object)
        .ok_or_else(|| "license public keys are missing".to_string())?;
    let binding_path = data_dir.join("license/binding.json");
    let lease_path = data_dir.join("license/lease.json");
    // A new desktop needs the sidecar in order to activate a trial or redeem
    // a code. An expired but authentic lease similarly needs the UI to refresh
    // it. The Python entitlement guard still blocks every scan in both cases.
    if !binding_path.exists() && !lease_path.exists() {
        return Ok(());
    }
    if !binding_path.is_file() || !lease_path.is_file() {
        return Err("license state is incomplete".to_string());
    }
    let binding: Value = read_json_value(&binding_path, "license binding")?;
    let lease: Value = read_json_value(&lease_path, "license lease")?;
    let payload = lease
        .get("payload")
        .and_then(Value::as_object)
        .ok_or_else(|| "license lease payload is invalid".to_string())?;
    let signature = lease
        .get("signature")
        .and_then(Value::as_str)
        .ok_or_else(|| "license lease signature is missing".to_string())?;
    if payload.get("type").and_then(Value::as_str) != Some("searchcar-license-lease")
        || payload.get("protocol_version").and_then(Value::as_i64) != Some(1)
    {
        return Err("license lease protocol is invalid".to_string());
    }
    let license_id = binding
        .get("license_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "license binding is invalid".to_string())?;
    let device_id = binding
        .get("device_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "license binding is invalid".to_string())?;
    if payload.get("license_id").and_then(Value::as_str) != Some(license_id)
        || payload.get("device_id").and_then(Value::as_str) != Some(device_id)
    {
        return Err("license lease is bound to another device".to_string());
    }
    let key_id = payload
        .get("key_id")
        .and_then(Value::as_str)
        .ok_or_else(|| "license signing key is missing".to_string())?;
    let public_key = public_keys
        .get(key_id)
        .and_then(Value::as_str)
        .ok_or_else(|| "license signing key is not trusted".to_string())?;
    let public_key: [u8; 32] = URL_SAFE_NO_PAD
        .decode(public_key)
        .map_err(|_| "license signing key encoding is invalid".to_string())?
        .try_into()
        .map_err(|_| "license signing key length is invalid".to_string())?;
    let signature = Signature::from_slice(
        &URL_SAFE_NO_PAD
            .decode(signature)
            .map_err(|_| "license signature encoding is invalid".to_string())?,
    )
    .map_err(|_| "license signature length is invalid".to_string())?;
    let canonical = canonical_json(&Value::Object(payload.clone()))?;
    VerifyingKey::from_bytes(&public_key)
        .map_err(|_| "license signing key is invalid".to_string())?
        .verify_strict(
            format!("{LEASE_MESSAGE_PREFIX}{canonical}").as_bytes(),
            &signature,
        )
        .map_err(|_| "license signature is invalid".to_string())?;
    for field in ["subscription_expires_at", "lease_expires_at"] {
        let value = payload
            .get(field)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("license {field} is missing"))?;
        DateTime::parse_from_rfc3339(value)
            .map_err(|_| format!("license {field} is invalid"))?;
    }
    Ok(())
}

fn read_json_value(path: &Path, label: &str) -> Result<Value, String> {
    let content = fs::read_to_string(path).map_err(|_| format!("{label} is unavailable"))?;
    serde_json::from_str(&content).map_err(|_| format!("{label} is invalid"))
}

/// Matches the Worker/Python canonical JSON contract rather than relying on a
/// serializer's object-order implementation detail.
fn canonical_json(value: &Value) -> Result<String, String> {
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => {
            serde_json::to_string(value).map_err(|_| "license payload is invalid".to_string())
        }
        Value::Number(number) => {
            if let Some(value) = number.as_i64() {
                Ok(value.to_string())
            } else if let Some(value) = number.as_u64() {
                Ok(value.to_string())
            } else {
                Err("license payload number is invalid".to_string())
            }
        }
        Value::Array(items) => items
            .iter()
            .map(canonical_json)
            .collect::<Result<Vec<_>, _>>()
            .map(|items| format!("[{}]", items.join(","))),
        Value::Object(items) => {
            let ordered: BTreeMap<_, _> = items.iter().collect();
            ordered
                .into_iter()
                .map(|(key, value)| {
                    Ok(format!(
                        "{}:{}",
                        serde_json::to_string(key)
                            .map_err(|_| "license payload key is invalid".to_string())?,
                        canonical_json(value)?
                    ))
                })
                .collect::<Result<Vec<_>, String>>()
                .map(|items| format!("{{{}}}", items.join(",")))
        }
    }
}

fn available_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|error| error.to_string())?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| error.to_string())
}

fn health_is_ready(port: u16) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().expect("valid loopback address"),
        Duration::from_millis(350),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(350)));
    if stream
        .write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.contains(" 200 ")
}

fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if health_is_ready(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(150));
    }
    false
}

fn request_sidecar_shutdown(port: u16, secret: &str) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().expect("valid loopback address"),
        Duration::from_millis(500),
    ) else {
        return false;
    };
    let request = format!(
        "POST /desktop/shutdown?token={secret} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.contains(" 202 ")
}

fn sidecar_request(app: &tauri::AppHandle, method: &str, path: &str) -> Option<String> {
    let (port, secret) = {
        let state = app.state::<SidecarState>();
        let guard = state.0.lock().ok()?;
        let process = guard.as_ref()?;
        (process.port, process.secret.clone())
    };
    let separator = if path.contains('?') { '&' } else { '?' };
    let target = format!("{path}{separator}token={secret}");
    let mut stream = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}").parse().ok()?,
        Duration::from_millis(600),
    )
    .ok()?;
    stream.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    let request = format!(
        "{method} {target} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.contains(" 200 ") {
        return None;
    }
    response
        .split_once("\r\n\r\n")
        .map(|(_, body)| body.trim().to_string())
}

fn scheduler_status(app: &tauri::AppHandle) -> Option<TrayStatus> {
    let body = sidecar_request(app, "GET", "/desktop/tray-status")?;
    let mut fields = body.splitn(3, '|');
    Some(TrayStatus {
        enabled: fields.next()? == "1",
        paused: fields.next()? == "1",
        next_run_at: fields.next().unwrap_or_default().to_string(),
    })
}

fn notify_sidecar_resumed(app: &tauri::AppHandle) {
    // The server marks a running scan as sleep-interrupted and makes the
    // overdue scheduler eligible again. It is intentionally best-effort: the
    // normal startup reconciliation handles a sidecar that is still booting.
    let _ = sidecar_request(app, "POST", "/desktop/scheduler/resumed");
}

fn consume_update_check_request(app: &tauri::AppHandle) -> bool {
    sidecar_request(app, "GET", "/desktop/update-check/consume").as_deref() == Some("1")
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn next_run_label(status: &TrayStatus) -> String {
    if !status.enabled {
        return "Next update: disabled".to_string();
    }
    if status.paused {
        return "Next update: paused".to_string();
    }
    if status.next_run_at.is_empty() {
        return "Next update: calculating…".to_string();
    }
    let compact = status.next_run_at.replace('T', " ");
    format!(
        "Next update (UTC): {}",
        compact.chars().take(16).collect::<String>()
    )
}

fn stop_sidecar(app: &tauri::AppHandle) {
    let state = app.state::<SidecarState>();
    let process = state.0.lock().ok().and_then(|mut child| child.take());
    if let Some(process) = process {
        if request_sidecar_shutdown(process.port, &process.secret) {
            let deadline = Instant::now() + Duration::from_secs(40);
            while Instant::now() < deadline && health_is_ready(process.port) {
                thread::sleep(Duration::from_millis(150));
            }
            if !health_is_ready(process.port) {
                return;
            }
        }
        let _ = process.child.kill();
    }
}

fn finish_confirmed_exit(app: &tauri::AppHandle) {
    if app
        .state::<LifecycleState>()
        .explicit_exit
        .swap(true, Ordering::SeqCst)
    {
        return;
    }
    let app_handle = app.clone();
    thread::spawn(move || {
        // The sidecar first marks queued work cancelled and active work as
        // cancellation-requested. It then waits for the worker to persist the
        // partial report before the native process exits.
        stop_sidecar(&app_handle);
        app_handle.exit(0);
    });
}

fn request_exit_confirmation(app: &tauri::AppHandle) {
    let lifecycle = app.state::<LifecycleState>();
    if lifecycle.explicit_exit.load(Ordering::SeqCst)
        || lifecycle
            .confirmation_open
            .swap(true, Ordering::SeqCst)
    {
        return;
    }
    show_main_window(app);
    let app_handle = app.clone();
    app.dialog()
        .message(
            "Текущий поиск будет остановлен. Уже обработанные машины останутся в отчёте.",
        )
        .title("Выйти из SearchCar?")
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Выйти и остановить поиск".to_string(),
            "Остаться".to_string(),
        ))
        .show(move |confirmed| {
            app_handle
                .state::<LifecycleState>()
                .confirmation_open
                .store(false, Ordering::SeqCst);
            if confirmed {
                finish_confirmed_exit(&app_handle);
            }
        });
}

fn finish_update_operation(app: &tauri::AppHandle) {
    app.state::<UpdaterState>()
        .busy
        .store(false, Ordering::SeqCst);
}

fn allow_programmatic_exit(app: &tauri::AppHandle) {
    app.state::<LifecycleState>()
        .explicit_exit
        .store(true, Ordering::SeqCst);
}

fn show_update_result(app: &tauri::AppHandle, title: &str, message: String, kind: MessageDialogKind) {
    app.dialog()
        .message(message)
        .title(title)
        .kind(kind)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

fn restart_after_update_failure(app: tauri::AppHandle, message: String) {
    let restart_handle = app.clone();
    app.dialog()
        .message(format!(
            "Не удалось установить обновление. SearchCar будет перезапущен.\n\n{message}"
        ))
        .title("Ошибка обновления")
        .kind(MessageDialogKind::Error)
        .buttons(MessageDialogButtons::Ok)
        .show(move |_| {
            allow_programmatic_exit(&restart_handle);
            restart_handle.restart();
        });
}

fn prompt_update_install(app: tauri::AppHandle, update: Update) {
    let version = update.version.clone();
    let notes = update
        .body
        .as_deref()
        .unwrap_or("Описание изменений не указано.")
        .chars()
        .take(2_000)
        .collect::<String>();
    let prompt_handle = app.clone();
    app.dialog()
        .message(format!(
            "Доступна версия {version}.\n\n{notes}\n\nСкачать и установить сейчас?"
        ))
        .title("Обновление SearchCar")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Скачать и установить".to_string(),
            "Позже".to_string(),
        ))
        .show(move |confirmed| {
            if !confirmed {
                finish_update_operation(&prompt_handle);
                return;
            }
            let install_handle = prompt_handle.clone();
            tauri::async_runtime::spawn(async move {
                let bytes = match update.download(|_, _| {}, || {}).await {
                    Ok(bytes) => bytes,
                    Err(error) => {
                        finish_update_operation(&install_handle);
                        show_update_result(
                            &install_handle,
                            "Ошибка обновления",
                            format!("Не удалось скачать или проверить подпись обновления.\n\n{error}"),
                            MessageDialogKind::Error,
                        );
                        return;
                    }
                };

                // The Windows installer cannot safely replace the bundled
                // sidecar while it is still running. The signature has already
                // been verified by `download`, so stop local work immediately
                // before handing the verified bytes to the native installer.
                stop_sidecar(&install_handle);
                if let Err(error) = update.install(bytes) {
                    finish_update_operation(&install_handle);
                    restart_after_update_failure(install_handle, error.to_string());
                    return;
                }

                finish_update_operation(&install_handle);
                allow_programmatic_exit(&install_handle);
                #[cfg(not(windows))]
                install_handle.restart();
                #[cfg(windows)]
                install_handle.exit(0);
            });
        });
}

fn start_update_check(app: &tauri::AppHandle, interactive: bool) {
    if app
        .state::<UpdaterState>()
        .busy
        .swap(true, Ordering::SeqCst)
    {
        if interactive {
            show_update_result(
                app,
                "Обновление SearchCar",
                "Проверка или установка обновления уже выполняется.".to_string(),
                MessageDialogKind::Info,
            );
        }
        return;
    }

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = match app_handle.updater() {
            Ok(updater) => updater.check().await.map_err(|error| error.to_string()),
            Err(error) => Err(error.to_string()),
        };
        match result {
            Ok(Some(update)) => prompt_update_install(app_handle, update),
            Ok(None) => {
                finish_update_operation(&app_handle);
                if interactive {
                    show_update_result(
                        &app_handle,
                        "Обновление SearchCar",
                        "Установлена актуальная версия SearchCar.".to_string(),
                        MessageDialogKind::Info,
                    );
                }
            }
            Err(error) => {
                finish_update_operation(&app_handle);
                if interactive {
                    show_update_result(
                        &app_handle,
                        "Ошибка обновления",
                        format!("Не удалось проверить наличие обновлений.\n\n{error}"),
                        MessageDialogKind::Error,
                    );
                }
            }
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState(Mutex::new(None)))
        .manage(LifecycleState {
            explicit_exit: AtomicBool::new(false),
            confirmation_open: AtomicBool::new(false),
        })
        .manage(UpdaterState {
            busy: AtomicBool::new(false),
        })
        .setup(|app| {
            app.handle()
                .plugin(tauri_plugin_updater::Builder::new().build())?;
            let port = available_port().map_err(std::io::Error::other)?;
            let secret: String = rand::rng()
                .sample_iter(&Alphanumeric)
                .take(64)
                .map(char::from)
                .collect();
            let data_dir = app.path().app_data_dir()?;
            let frontend_dir = app.path().resource_dir()?.join("desktop-ui");
            let browser_dir = app.path().resource_dir()?.join("browsers");
            let playwright_driver_dir = app
                .path()
                .resource_dir()?
                .join("playwright-driver");
            let license_config = app.path().resource_dir()?.join("license-service.json");
            std::fs::create_dir_all(&data_dir)?;
            verify_required_lease(&license_config, &data_dir)
                .map_err(|error| std::io::Error::other(format!("license preflight failed: {error}")))?;

            let mut sidecar_arguments = vec![
                "serve".to_string(),
                "--host".to_string(),
                "127.0.0.1".to_string(),
                "--port".to_string(),
                port.to_string(),
                "--data-dir".to_string(),
                data_dir.to_string_lossy().into_owned(),
                "--frontend-dir".to_string(),
                frontend_dir.to_string_lossy().into_owned(),
                "--browser-dir".to_string(),
                browser_dir.to_string_lossy().into_owned(),
                "--playwright-driver-dir".to_string(),
                playwright_driver_dir.to_string_lossy().into_owned(),
            ];
            if option_env!("SEARCHCAR_MACOS_LOCAL_KEY_FALLBACK") == Some("1") {
                // A command-line flag survives the onefile sidecar boundary
                // even when a bundled macOS process drops a custom env var.
                sidecar_arguments.push("--allow-device-key-file-fallback".to_string());
            }
            let command = app
                .shell()
                .sidecar("searchcar-core")?
                .args(sidecar_arguments)
                .env("SEARCHCAR_DESKTOP_SESSION_SECRET", &secret)
                .env(
                    "SEARCHCAR_DESKTOP_PARENT_PID",
                    std::process::id().to_string(),
                )
                .env(
                    "SEARCHCAR_ALLOW_DEVICE_KEY_FILE_FALLBACK",
                    option_env!("SEARCHCAR_MACOS_LOCAL_KEY_FALLBACK").unwrap_or("0"),
                )
                .env("SEARCHCAR_LICENSE_CONFIG_FILE", license_config);
            let (mut events, child) = command.spawn()?;
            *app.state::<SidecarState>().0.lock().expect("sidecar state") =
                Some(SidecarProcess {
                    child,
                    port,
                    secret: secret.clone(),
                });

            let open_item = MenuItem::with_id(app, "open", "Open SearchCar", true, None::<&str>)?;
            let next_item = MenuItem::with_id(
                app,
                "next",
                "Next update: calculating…",
                false,
                None::<&str>,
            )?;
            let pause_item = MenuItem::with_id(
                app,
                "pause",
                "Pause automatic updates",
                false,
                None::<&str>,
            )?;
            let update_item = MenuItem::with_id(
                app,
                "update",
                "Check for updates…",
                true,
                None::<&str>,
            )?;
            let exit_item = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[&open_item, &next_item, &pause_item, &update_item, &exit_item],
            )?;
            let next_for_menu = next_item.clone();
            let pause_for_menu = pause_item.clone();
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("application icon").clone())
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "open" => show_main_window(app),
                    "pause" => {
                        let _ = sidecar_request(app, "POST", "/desktop/scheduler/toggle");
                        if let Some(status) = scheduler_status(app) {
                            let _ = next_for_menu.set_text(next_run_label(&status));
                            let _ = pause_for_menu.set_text(if status.paused {
                                "Resume automatic updates"
                            } else {
                                "Pause automatic updates"
                            });
                        }
                    }
                    "exit" => {
                        request_exit_confirmation(app);
                    }
                    "update" => start_update_check(app, true),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

            tauri::async_runtime::spawn(async move {
                while events.recv().await.is_some() {}
            });

            let app_handle = app.handle().clone();
            let next_for_poll = next_item.clone();
            let pause_for_poll = pause_item.clone();
            thread::spawn(move || {
                if !wait_for_health(port, Duration::from_secs(20)) {
                    stop_sidecar(&app_handle);
                    app_handle.exit(1);
                    return;
                }
                let target =
                    format!("http://127.0.0.1:{port}/desktop/bootstrap?token={secret}");
                let window_handle = app_handle.clone();
                let _ = app_handle.run_on_main_thread(move || {
                    let _ = WebviewWindowBuilder::new(
                        &window_handle,
                        "main",
                        WebviewUrl::External(target.parse().expect("valid desktop URL")),
                    )
                    .title("SearchCar Desktop")
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(980.0, 680.0)
                    .build();
                });

                start_update_check(&app_handle, false);
                let update_request_handle = app_handle.clone();
                thread::spawn(move || loop {
                    if !health_is_ready(port) {
                        break;
                    }
                    if consume_update_check_request(&update_request_handle) {
                        start_update_check(&update_request_handle, true);
                    }
                    thread::sleep(Duration::from_millis(500));
                });

                let periodic_update_handle = app_handle.clone();
                thread::spawn(move || loop {
                    thread::sleep(UPDATE_CHECK_INTERVAL);
                    if !health_is_ready(port) {
                        break;
                    }
                    start_update_check(&periodic_update_handle, false);
                });

                let status_handle = app_handle.clone();
                thread::spawn(move || loop {
                    if !health_is_ready(port) {
                        break;
                    }
                    if let Some(status) = scheduler_status(&status_handle) {
                        let _ = next_for_poll.set_text(next_run_label(&status));
                        let _ = pause_for_poll.set_enabled(status.enabled);
                        let _ = pause_for_poll.set_text(if status.paused {
                            "Resume automatic updates"
                        } else {
                            "Pause automatic updates"
                        });
                    }
                    thread::sleep(Duration::from_secs(15));
                });
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                if window
                    .app_handle()
                    .state::<LifecycleState>()
                    .explicit_exit
                    .load(Ordering::SeqCst)
                {
                    return;
                }
                request_exit_confirmation(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to run SearchCar Desktop");
    app.run(|app, event| match event {
        RunEvent::ExitRequested { api, .. } => {
            if app
                .state::<LifecycleState>()
                .explicit_exit
                .load(Ordering::SeqCst)
            {
                stop_sidecar(app);
            } else {
                api.prevent_exit();
                request_exit_confirmation(app);
            }
        }
        RunEvent::Exit => stop_sidecar(app),
        RunEvent::Resumed => notify_sidecar_resumed(app),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::{Duration as ChronoDuration, Utc};
    use ed25519_dalek::{Signer, SigningKey};
    use serde_json::json;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn temporary_directory() -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock is after epoch")
            .as_nanos();
        std::env::temp_dir().join(format!("searchcar-license-test-{}-{nonce}", std::process::id()))
    }

    #[test]
    fn required_preflight_accepts_only_a_matching_signed_unexpired_lease() {
        let directory = temporary_directory();
        let data_dir = directory.join("data");
        let config_path = directory.join("license-service.json");
        let license_id = "018f6ac2-8c44-7df0-8f6d-2d34af37b337";
        let device_id = "028f6ac2-8c44-7df0-8f6d-2d34af37b337";
        let signing_key = SigningKey::from_bytes(&[7_u8; 32]);
        let public_key = URL_SAFE_NO_PAD.encode(signing_key.verifying_key().as_bytes());
        let expires_at = (Utc::now() + ChronoDuration::hours(1)).to_rfc3339();
        fs::create_dir_all(&directory).expect("temporary directory");
        let payload = json!({
            "type": "searchcar-license-lease",
            "protocol_version": 1,
            "key_id": "test-v1",
            "server_time": Utc::now().to_rfc3339(),
            "issued_at": Utc::now().to_rfc3339(),
            "license_id": license_id,
            "device_id": device_id,
            "license_type": "SUBSCRIPTION",
            "subscription_expires_at": expires_at,
            "lease_expires_at": expires_at,
            "entitlements": {"search": true}
        });
        let canonical = canonical_json(&payload).expect("canonical payload");
        let signature = URL_SAFE_NO_PAD.encode(
            signing_key.sign(format!("{LEASE_MESSAGE_PREFIX}{canonical}").as_bytes()).to_bytes(),
        );
        fs::write(
            &config_path,
            json!({
                "enforcement": "required",
                "public_keys": {"test-v1": public_key}
            })
            .to_string(),
        )
        .expect("configuration");
        assert!(verify_required_lease(&config_path, &data_dir).is_ok());
        fs::create_dir_all(data_dir.join("license")).expect("license directory");
        fs::write(
            data_dir.join("license/binding.json"),
            json!({"license_id": license_id, "device_id": device_id}).to_string(),
        )
        .expect("binding");
        fs::write(
            data_dir.join("license/lease.json"),
            json!({"payload": payload, "signature": signature}).to_string(),
        )
        .expect("lease");

        assert!(verify_required_lease(&config_path, &data_dir).is_ok());

        fs::write(
            data_dir.join("license/binding.json"),
            json!({"license_id": license_id, "device_id": "different-device"}).to_string(),
        )
        .expect("tampered binding");
        assert!(verify_required_lease(&config_path, &data_dir).is_err());
        fs::remove_dir_all(directory).expect("temporary directory cleanup");
    }
}
