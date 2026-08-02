use rand::{distr::Alphanumeric, Rng};
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};


struct SidecarState(Mutex<Option<CommandChild>>);


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


fn stop_sidecar(app: &tauri::AppHandle) {
    let state = app.state::<SidecarState>();
    let process = state.0.lock().ok().and_then(|mut child| child.take());
    if let Some(process) = process {
        let _ = process.kill();
    }
}


#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            let port = available_port().map_err(std::io::Error::other)?;
            let secret: String = rand::rng()
                .sample_iter(&Alphanumeric)
                .take(64)
                .map(char::from)
                .collect();
            let data_dir = app.path().app_data_dir()?;
            let frontend_dir = app.path().resource_dir()?.join("desktop-ui");
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
                ])
                .env("SEARCHCAR_DESKTOP_SESSION_SECRET", &secret);
            let (mut events, child) = command.spawn()?;
            *app.state::<SidecarState>().0.lock().expect("sidecar state") = Some(child);

            tauri::async_runtime::spawn(async move {
                while events.recv().await.is_some() {}
            });

            let app_handle = app.handle().clone();
            thread::spawn(move || {
                if !wait_for_health(port, Duration::from_secs(20)) {
                    stop_sidecar(&app_handle);
                    app_handle.exit(1);
                    return;
                }
                let target = format!(
                    "http://127.0.0.1:{port}/desktop/bootstrap?token={secret}"
                );
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
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                stop_sidecar(window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run SearchCar Desktop");
}
