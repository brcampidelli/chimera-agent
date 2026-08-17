// Prevents an extra console window on Windows in release; does nothing elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Chimera desktop shell.
//!
//! A thin native window + tray around the SAME server the pip/CLI path runs. On startup it launches
//! the bundled, PyInstaller-frozen `chimera-backend` sidecar with `--no-open --port 0
//! --emit-port-file <tmp>`, waits for the free-port URL the backend writes, then points the webview
//! at that localhost origin. The SPA is served BY the sidecar (same origin), so its relative `/api`
//! calls just work — no divergent server code, no base-URL rewiring. The sidecar is killed on exit.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
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
/// Lines the backend's stderr is worth keeping. A traceback is the last ~30; the rest is boot noise,
/// and an unbounded buffer is how a chatty backend eats memory over a long session.
const STDERR_KEEP: usize = 80;

/// Read the child's stderr on a thread, keeping the last [`STDERR_KEEP`] lines.
///
/// A thread rather than a read-on-failure: an unread pipe fills, and a process blocked writing into
/// a full pipe never gets to say the thing we wanted to hear. Diagnosing a hang by causing one is
/// the wrong trade.
fn drain_stderr(child: &mut Child) -> Arc<Mutex<VecDeque<String>>> {
    let tail = Arc::new(Mutex::new(VecDeque::with_capacity(STDERR_KEEP)));
    let Some(stderr) = child.stderr.take() else {
        return tail;
    };
    let sink = Arc::clone(&tail);
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            // A poisoned lock here must not take the reader down: the backend keeps running, and
            // losing the tail is a worse-diagnosed failure, not a second failure.
            if let Ok(mut buf) = sink.lock() {
                if buf.len() == STDERR_KEEP {
                    buf.pop_front();
                }
                buf.push_back(line);
            }
        }
    });
    tail
}

/// Write what we know about a failed startup, and return the path.
///
/// Best-effort throughout: this runs on the path where something already went wrong, and a
/// diagnostics writer that can itself fail the startup would be the joke writing itself. Every
/// error here degrades to a less complete file, never to a second failure.
fn write_startup_report(
    data_dir: &Path,
    exe: &Path,
    child: &mut Child,
    tail: &Arc<Mutex<VecDeque<String>>>,
    why: &str,
) -> PathBuf {
    let path = data_dir.join("startup-failure.txt");
    let exit = match child.try_wait() {
        Ok(Some(status)) => format!("{status}"),
        Ok(None) => "still running (killed after this report)".to_string(),
        Err(e) => format!("unknown: {e}"),
    };
    let stderr = tail
        .lock()
        .map(|buf| buf.iter().cloned().collect::<Vec<_>>().join("\n"))
        .unwrap_or_else(|_| "<stderr buffer unavailable>".to_string());
    let body = format!(
        "Chimera desktop — backend failed to start\n\
         version: {}\n\
         backend: {}\n\
         pid: {}\n\
         exit: {exit}\n\
         reason: {why}\n\
         \n\
         --- last {STDERR_KEEP} lines of backend stderr ---\n{}\n",
        env!("CARGO_PKG_VERSION"),
        exe.display(),
        child.id(),
        if stderr.is_empty() { "<the backend printed nothing>" } else { &stderr },
    );
    let _ = std::fs::create_dir_all(data_dir);
    let _ = std::fs::write(&path, body);
    path
}

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

    // stderr is PIPED so a backend that dies on startup leaves its last words somewhere. It used to
    // be inherited, which on a windowed Windows build means discarded: the sidecar would print a
    // traceback into a console that does not exist, `start_sidecar` would return a timeout with no
    // detail, and the user would click the icon and get nothing at all — no window, no log, no file.
    //
    // Four competing agent apps each hardened this same step, independently and in four different
    // frameworks. That is not convergent taste; it is the failure everyone met in the field.
    let mut child = Command::new(&exe)
        .args(["--no-open", "--port", &wanted_arg, "--emit-port-file"])
        .arg(&port_file)
        .env("CHIMERA_HOME", data_dir.join("data"))
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("failed to launch backend {exe:?}: {e}"))?;

    // Drain it on a thread. Reading it only after a failure would deadlock instead of diagnosing:
    // a pipe nobody reads fills, and a backend blocked writing to a full pipe never reaches the
    // line that would have told us what was wrong.
    let stderr_tail = drain_stderr(&mut child);

    // The frozen exe unpacks + boots; give it a generous window before giving up.
    let url = wait_for_url(&port_file, Duration::from_secs(45))
        .and_then(|url| wait_for_listening(&url, Duration::from_secs(30)).map(|()| url))
        .map_err(|why| {
            // Whatever the backend managed to say, plus the exit code if it already died — written
            // BEFORE this error propagates, because everything above it is about to be unwound.
            let report = write_startup_report(&data_dir, &exe, &mut child, &stderr_tail, &why);
            let _ = child.kill();
            format!("{why}\n\nDiagnostics written to:\n{}", report.display())
        })?;
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
    use super::{drain_stderr, port_of, remembered_port, write_startup_report, STDERR_KEEP};
    use std::process::{Command, Stdio};

    /// A backend that dies on startup must leave its last words behind.
    ///
    /// This is the regression the whole change exists for: stderr used to be inherited, which on a
    /// windowed Windows build means discarded — the traceback went to a console that does not exist.
    #[test]
    fn a_dying_backend_leaves_its_stderr_in_the_report() {
        let dir = std::env::temp_dir().join(format!("chimera-startup-test-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);

        let mut child = Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) {
                vec!["/C", "echo ModuleNotFoundError: no module named chimera 1>&2 & exit 3"]
            } else {
                vec!["-c", "echo 'ModuleNotFoundError: no module named chimera' >&2; exit 3"]
            })
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn the stand-in backend");

        let tail = drain_stderr(&mut child);
        let _ = child.wait();
        // The draining thread races the exit; give it a moment to finish the last line.
        std::thread::sleep(std::time::Duration::from_millis(300));

        let path = write_startup_report(
            &dir,
            std::path::Path::new("chimera-backend"),
            &mut child,
            &tail,
            "timed out waiting for the port file",
        );
        let body = std::fs::read_to_string(&path).expect("the report was written");

        assert!(body.contains("ModuleNotFoundError"), "the backend's own words are missing: {body}");
        assert!(body.contains("timed out waiting"), "the reason is missing");
        assert!(body.contains("exit"), "the exit status is missing");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A backend that says nothing must still produce a readable file, not an empty one.
    #[test]
    fn a_silent_backend_still_gets_a_report_that_says_so() {
        let dir = std::env::temp_dir().join(format!("chimera-silent-test-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let mut child = Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) { vec!["/C", "exit 1"] } else { vec!["-c", "exit 1"] })
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn");
        let tail = drain_stderr(&mut child);
        let _ = child.wait();
        let path = write_startup_report(&dir, std::path::Path::new("x"), &mut child, &tail, "why");
        let body = std::fs::read_to_string(&path).unwrap();
        assert!(
            body.contains("the backend printed nothing"),
            "an empty tail must say so rather than leave a blank section: {body}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The three tests above are HELPER tests, and I found that out by trying to break them.
    ///
    /// Removing `.stderr(Stdio::piped())` from `start_sidecar` left all three green, because each
    /// spawns its own child and pipes it explicitly — they prove `drain_stderr` and
    /// `write_startup_report` work, and say nothing about whether the sidecar path calls them. That
    /// is the same "tests the class, not the wiring" defect this project has a skill card about.
    ///
    /// `start_sidecar` takes `&tauri::App`, which a unit test cannot construct, so the behavioural
    /// version would mean restructuring it to accept a path. Worth doing; not worth blocking the
    /// fix on. Until then this is a source-level assertion — weaker than behaviour, but it fails on
    /// exactly the regression that matters and is honest about being a stand-in.
    #[test]
    fn the_sidecar_actually_pipes_its_stderr() {
        let source = include_str!("main.rs");
        // Cut the tests off FIRST. Two earlier versions of this window passed with the fix reverted:
        // one took everything after the function name (reaching this very test, which mentions the
        // string), and one bounded at the next top-level `fn` — but `mod tests` sits between
        // `start_sidecar` and `kill_sidecar`, so that window swallowed the three tests below, and
        // they pipe stderr themselves. A search window wide enough to contain its own answer proves
        // nothing, and I wrote two of them before checking.
        let production = source
            .split_once("#[cfg(test)]")
            .expect("the test module marks where production code ends")
            .0;
        let body = production
            .split_once("fn start_sidecar")
            .expect("start_sidecar exists")
            .1;
        // `start_sidecar` is the last function before the tests today; if one is added after it,
        // this narrows further rather than opening up.
        let spawn = body.split_once("\nfn ").map_or(body, |(head, _)| head);
        assert!(
            spawn.contains(".stderr(Stdio::piped())"),
            "start_sidecar stopped piping stderr — a backend that dies on startup is silent again"
        );
        assert!(
            spawn.contains("write_startup_report"),
            "start_sidecar stopped writing a report on failure"
        );
    }

    /// The buffer is bounded: a chatty backend must not grow it without limit.
    #[test]
    fn the_stderr_tail_is_bounded() {
        let dir = std::env::temp_dir().join(format!("chimera-bound-test-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let script = format!("for i in $(seq 1 {}); do echo line$i >&2; done", STDERR_KEEP * 3);
        let win = format!("for /L %i in (1,1,{}) do @echo line%i 1>&2", STDERR_KEEP * 3);
        let mut child = Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) { vec!["/C", &win] } else { vec!["-c", &script] })
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn");
        let tail = drain_stderr(&mut child);
        let _ = child.wait();
        std::thread::sleep(std::time::Duration::from_millis(500));
        assert!(tail.lock().unwrap().len() <= STDERR_KEEP);
        let _ = std::fs::remove_dir_all(&dir);
    }

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
            //
            // The failure branch is the point. This used to be a bare `?`, which propagated to the
            // `.expect()` on `build()` — and under `windows_subsystem = "windows"` a panic writes to
            // a stderr that does not exist. Clicking the icon did nothing at all: no window, no
            // console, no file. "Nothing happens" is the least diagnosable failure a desktop app can
            // have, and it was ours on the platform this project is developed on.
            let (child, url) = match start_sidecar(app) {
                Ok(pair) => pair,
                Err(why) => {
                    // Blocking, so the process cannot exit before the user has read it.
                    app.dialog()
                        .message(&why)
                        .title("Chimera could not start")
                        .buttons(MessageDialogButtons::Ok)
                        .blocking_show();
                    return Err(Box::<dyn std::error::Error>::from(why));
                }
            };
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
