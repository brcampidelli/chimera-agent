// Prevents an extra console window on Windows in release; does nothing elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Chimera desktop shell.
//!
//! A thin native window + tray around the SAME server the pip/CLI path runs. On startup it launches
//! the bundled, PyInstaller-frozen `chimera-backend` sidecar with `--no-open --port 0
//! --emit-port-file <tmp>`, waits for the free-port URL the backend writes, then points the webview
//! at that localhost origin. The SPA is served BY the sidecar (same origin), so its relative `/api`
//! calls just work — no divergent server code, no base-URL rewiring. The sidecar is killed on exit.

use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// Holds the sidecar child so it can be killed when the app exits.
struct Sidecar(Mutex<Option<Child>>);

/// Resolve the bundled sidecar executable inside the app's resource dir.
fn sidecar_path(app: &tauri::App) -> Result<PathBuf, String> {
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let exe = if cfg!(windows) { "chimera-backend.exe" } else { "chimera-backend" };
    Ok(resource_dir.join("sidecar-dist").join("chimera-backend").join(exe))
}

/// Poll `port_file` until the backend writes its `http://host:port` URL (or time out).
fn wait_for_url(port_file: &Path, timeout: Duration) -> Result<String, String> {
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(s) = std::fs::read_to_string(port_file) {
            let s = s.trim();
            if s.starts_with("http") {
                return Ok(s.to_string());
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err("backend did not report its URL in time".into())
}

/// Wait until the backend's TCP port accepts a connection, so the window doesn't load before it binds.
fn wait_for_listening(url: &str, timeout: Duration) -> Result<(), String> {
    // Parse "http://host:port" without pulling an HTTP client dependency.
    let hostport = url.strip_prefix("http://").unwrap_or(url);
    let hostport = hostport.split('/').next().unwrap_or(hostport);
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect_timeout(
            &hostport
                .to_socket_addrs_first()
                .ok_or("could not resolve backend address")?,
            Duration::from_millis(500),
        )
        .is_ok()
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    Err("backend port never opened".into())
}

/// Tiny helper: resolve the first socket address for a "host:port" string.
trait FirstAddr {
    fn to_socket_addrs_first(&self) -> Option<std::net::SocketAddr>;
}
impl FirstAddr for str {
    fn to_socket_addrs_first(&self) -> Option<std::net::SocketAddr> {
        use std::net::ToSocketAddrs;
        self.to_socket_addrs().ok().and_then(|mut it| it.next())
    }
}

/// Launch the sidecar and return the running child plus the URL it reported.
fn start_sidecar(app: &tauri::App) -> Result<(Child, String), String> {
    let exe = sidecar_path(app)?;
    if !exe.exists() {
        return Err(format!("bundled backend not found at {exe:?}"));
    }
    let port_file = std::env::temp_dir().join(format!("chimera-app-port-{}.txt", std::process::id()));
    let _ = std::fs::remove_file(&port_file);

    let child = Command::new(&exe)
        .args(["--no-open", "--port", "0", "--emit-port-file"])
        .arg(&port_file)
        .spawn()
        .map_err(|e| format!("failed to launch backend {exe:?}: {e}"))?;

    // The frozen exe unpacks + boots; give it a generous window before giving up.
    let url = wait_for_url(&port_file, Duration::from_secs(45))?;
    wait_for_listening(&url, Duration::from_secs(30))?;
    let _ = std::fs::remove_file(&port_file);
    Ok((child, url))
}

fn kill_sidecar(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Sidecar>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            // Bring up the backend, then open the window at its origin.
            let (child, url) = start_sidecar(app).map_err(|e| -> Box<dyn std::error::Error> {
                Box::<dyn std::error::Error>::from(e)
            })?;
            app.state::<Sidecar>().0.lock().unwrap().replace(child);

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("Chimera")
                .inner_size(1200.0, 800.0)
                .min_inner_size(760.0, 520.0)
                .build()?;

            // Tray with a Quit item (kills the sidecar via the exit hook below).
            let quit = MenuItem::with_id(app, "quit", "Quit Chimera", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quit])?;
            let icon = app.default_window_icon().cloned();
            let mut tray = TrayIconBuilder::new().menu(&menu).tooltip("Chimera");
            if let Some(icon) = icon {
                tray = tray.icon(icon);
            }
            tray.on_menu_event(|app, event| {
                if event.id.as_ref() == "quit" {
                    app.exit(0);
                }
            })
            .build(app)?;

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Chimera desktop app")
        .run(|app_handle, event| {
            // Whatever ends the app, take the sidecar down with it. Window-close fires ExitRequested;
            // the tray "Quit" (app.exit) fires Exit — cover both so no orphan backend survives.
            match event {
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                    kill_sidecar(app_handle);
                }
                _ => {}
            }
        });
}
