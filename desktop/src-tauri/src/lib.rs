use rand::{distr::Alphanumeric, Rng};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
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
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct SidecarProcess {
    child: CommandChild,
    port: u16,
    secret: String,
}

struct SidecarState(Mutex<Option<SidecarProcess>>);

struct LifecycleState {
    explicit_exit: AtomicBool,
}

#[derive(Default)]
struct TrayStatus {
    enabled: bool,
    paused: bool,
    next_run_at: String,
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState(Mutex::new(None)))
        .manage(LifecycleState {
            explicit_exit: AtomicBool::new(false),
        })
        .setup(|app| {
            let port = available_port().map_err(std::io::Error::other)?;
            let secret: String = rand::rng()
                .sample_iter(&Alphanumeric)
                .take(64)
                .map(char::from)
                .collect();
            let data_dir = app.path().app_data_dir()?;
            let frontend_dir = app.path().resource_dir()?.join("desktop-ui");
            let browser_dir = app.path().resource_dir()?.join("browsers");
            std::fs::create_dir_all(&data_dir)?;

            let command = app
                .shell()
                .sidecar("searchcar-core")?
                .args(vec![
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
                ])
                .env("SEARCHCAR_DESKTOP_SESSION_SECRET", &secret);
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
            let exit_item = MenuItem::with_id(app, "exit", "Exit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_item, &next_item, &pause_item, &exit_item])?;
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
                        app.state::<LifecycleState>()
                            .explicit_exit
                            .store(true, Ordering::SeqCst);
                        stop_sidecar(app);
                        app.exit(0);
                    }
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
                if window
                    .app_handle()
                    .state::<LifecycleState>()
                    .explicit_exit
                    .load(Ordering::SeqCst)
                {
                    return;
                }
                if scheduler_status(window.app_handle())
                    .map(|status| status.enabled)
                    .unwrap_or(false)
                {
                    api.prevent_close();
                    let _ = window.hide();
                } else {
                    api.prevent_close();
                    window
                        .app_handle()
                        .state::<LifecycleState>()
                        .explicit_exit
                        .store(true, Ordering::SeqCst);
                    stop_sidecar(window.app_handle());
                    window.app_handle().exit(0);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to run SearchCar Desktop");
    app.run(|app, event| {
        if matches!(event, RunEvent::ExitRequested { .. }) {
            stop_sidecar(app);
        }
    });
}
