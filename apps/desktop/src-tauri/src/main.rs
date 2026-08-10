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
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::UpdaterExt;

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

/// The port this install used last time, or 0 to let the OS choose.
///
/// Asking for the same port again is not an optimisation — it is what makes the app's own storage
/// survive a restart. The window loads an `http://127.0.0.1:<port>` origin, and browsers partition
/// `localStorage` by scheme+host+**port**, so an OS-assigned port every launch means a new origin
/// every launch: theme, chosen workspace, project list and language are all written to a store the
/// next launch cannot see. The code that saves them says they are "remembered across launches", and
/// with a rotating port that sentence is false.
///
/// Anything below 1024 is ignored: a memo claiming a privileged port cannot have come from us.
fn remembered_port(memo: &Path) -> u16 {
    std::fs::read_to_string(memo)
        .ok()
        .and_then(|s| s.trim().parse::<u16>().ok())
        .filter(|p| *p >= 1024)
        .unwrap_or(0)
}

/// The port out of `http://host:port`, if it parses.
fn port_of(url: &str) -> Option<u16> {
    url.trim_end_matches('/').rsplit(':').next()?.parse().ok()
}

/// Launch the sidecar and return the running child plus the URL it reported.
fn start_sidecar(app: &tauri::App) -> Result<(Child, String), String> {
    let exe = sidecar_path(app)?;
    if !exe.exists() {
        return Err(format!("bundled backend not found at {exe:?}"));
    }
    let port_file = std::env::temp_dir().join(format!("chimera-app-port-{}.txt", std::process::id()));
    let _ = std::fs::remove_file(&port_file);

    // Where this install keeps its data — memory, run receipts, sessions, traces.
    //
    // `Settings.home` defaults to the RELATIVE path `.chimera`, which is right for the CLI (data
    // sits beside the project you ran it in) and wrong for a packaged app, which has no meaningful
    // working directory. Without this the sidecar inherited whatever CWD the launcher happened to
    // give it: the install directory under Program Files on Windows (not writable), or a different
    // folder per shortcut — so the same app could show two different histories depending on how it
    // was opened, and a fresh install could fail to write at all.
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|e| format!("cannot create {data_dir:?}: {e}"))?;

    // Ask for the port we used last time so the window keeps its origin — see `remembered_port`.
    // Asking is all this does: the backend falls back to an OS-assigned free port when the requested
    // one is taken, so a port claimed by something else (or by a second copy of this app) costs a
    // fresh origin once, never a failure to start.
    let memo = data_dir.join("last-port.txt");
    let wanted = remembered_port(&memo);
    let wanted_arg = wanted.to_string();

    let child = Command::new(&exe)
        .args(["--no-open", "--port", &wanted_arg, "--emit-port-file"])
        .arg(&port_file)
        .env("CHIMERA_HOME", data_dir.join("data"))
        .spawn()
        .map_err(|e| format!("failed to launch backend {exe:?}: {e}"))?;

    // The frozen exe unpacks + boots; give it a generous window before giving up.
    let url = wait_for_url(&port_file, Duration::from_secs(45))?;
    wait_for_listening(&url, Duration::from_secs(30))?;
    let _ = std::fs::remove_file(&port_file);

    // Remember what we actually got, which is not always what we asked for. Best-effort on purpose:
    // an unwritable memo means the next launch picks a fresh port, which is the behaviour we had
    // before — worth degrading to, never worth refusing to start over.
    if let Some(actual) = port_of(&url) {
        if actual != wanted {
            let _ = std::fs::write(&memo, actual.to_string());
        }
    }
    Ok((child, url))
}

#[cfg(test)]
mod tests {
    use super::{port_of, remembered_port};

    #[test]
    fn a_url_yields_its_port() {
        assert_eq!(port_of("http://127.0.0.1:51234"), Some(51234));
        assert_eq!(port_of("http://127.0.0.1:51234/"), Some(51234));
        assert_eq!(port_of("http://127.0.0.1"), None);
    }

    #[test]
    fn a_missing_or_nonsense_memo_means_let_the_os_choose() {
        let dir = std::env::temp_dir().join("chimera-port-memo-test");
        std::fs::create_dir_all(&dir).unwrap();
        let memo = dir.join("last-port.txt");

        let _ = std::fs::remove_file(&memo);
        assert_eq!(remembered_port(&memo), 0);

        std::fs::write(&memo, "not a port").unwrap();
        assert_eq!(remembered_port(&memo), 0);

        // A privileged port cannot have come from an OS-assigned bind, so it is not ours to reuse.
        std::fs::write(&memo, "80").unwrap();
        assert_eq!(remembered_port(&memo), 0);

        std::fs::write(&memo, "51234\n").unwrap();
        assert_eq!(remembered_port(&memo), 51234);
        let _ = std::fs::remove_file(&memo);
    }
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

/// Check GitHub for a signed update and, if the user consents, install it and relaunch.
///
/// Honest-by-construction: the signature is verified against the embedded pubkey by the updater
/// plugin (we never disable it); nothing installs without the explicit confirm dialog; and every
/// failure path (offline, no release, rate-limit, bad signature) returns `Err` so the caller can
/// stay silent — the user is never nagged and the app never crashes over an update check.
async fn check_for_update(app: tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    // `check()` fetches the manifest and verifies the signature; `None` = already up to date.
    let Some(update) = app.updater()?.check().await? else {
        return Ok(());
    };

    let confirmed = app
        .dialog()
        .message(format!(
            "A new version (v{}) is available. Download and install now? The app will restart.",
            update.version
        ))
        .title("Chimera update available")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Update & Restart".to_string(),
            "Later".to_string(),
        ))
        // Safe here: this runs inside `tauri::async_runtime::spawn` (a worker thread), not the main
        // thread, so blocking for the answer doesn't freeze the UI.
        .blocking_show();

    if !confirmed {
        return Ok(());
    }

    // Download (signature already verified by `check`) then install. No-op progress callbacks — the
    // native install dialog conveys progress; we keep the flow minimal.
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await?;

    // `restart()` diverges (`-> !`): it fires ExitRequested/Exit, so the run-loop hook below still
    // kills the sidecar before the fresh process launches.
    app.restart();
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(None)))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
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

            // Fire-and-forget update check. Any error (offline, no update, verification failure) is
            // swallowed — the check must never nag or crash. The pip/web "update signal" is separate
            // and unaffected; this is the native in-place path.
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let _ = check_for_update(handle).await;
            });

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
