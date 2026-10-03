// Prevents an extra console window on Windows in release; does nothing elsewhere.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Chimera desktop shell.
//!
//! A thin native window + tray around the SAME server the pip/CLI path runs. On startup it launches
//! the bundled, PyInstaller-frozen `chimera-backend` sidecar with `--no-open --port 0
//! --emit-port-file <tmp>`, waits for the free-port URL the backend writes, then points the webview
//! at that localhost origin. The SPA is served BY the sidecar (same origin), so its relative `/api`
//! calls just work — no divergent server code, no base-URL rewiring. The sidecar is killed on exit.

mod prefs;
mod sidecar_http;

use std::collections::VecDeque;
use std::io::{BufRead, BufReader};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::webview::NewWindowResponse;
use tauri::{Manager, UserAttentionType, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};
use tauri_plugin_updater::UpdaterExt;

use crate::prefs::{OnClose, Prefs};
use crate::sidecar_http::Fetch;

/// A running backend: the process, the origin it serves, and the last thing it said.
///
/// The stderr tail is kept for the whole life of the process, not just its startup. A backend that
/// failed to start already left a report; one that died an hour into a session used to leave
/// nothing at all, and "it just stopped" is not a diagnosis anybody can act on.
struct Backend {
    child: Child,
    url: String,
    stderr: Arc<Mutex<VecDeque<String>>>,
}

/// The sidecar the app is holding, and whether the app is on its way out.
///
/// `stopping` is not ceremony. The supervisor spawns a replacement WITHOUT holding the lock — a
/// cold start can take 45 seconds, and holding the mutex across it would freeze the exit hook for
/// exactly that long — so there is a window in which the user can quit mid-restart. Without this
/// flag that window ends with a backend nobody owns still running after the window is gone.
struct Sidecar {
    backend: Mutex<Option<Backend>>,
    stopping: AtomicBool,
}

/// Where the backend is, where its data goes, and where it reports the port it bound.
///
/// Resolved once from `&tauri::App` so every function below takes plain paths. That is not
/// tidiness: `start_sidecar` used to take `&tauri::App`, which a unit test cannot construct, so the
/// startup path could only be checked by asserting on the SOURCE of this file. Now it is called for
/// real by its own tests, and by the supervisor, which has no `App` either.
#[derive(Clone)]
struct Paths {
    exe: PathBuf,
    data_dir: PathBuf,
    port_file: PathBuf,
}

/// Resolve the bundled sidecar executable inside the app's resource dir.
fn sidecar_path(app: &tauri::App) -> Result<PathBuf, String> {
    let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
    let exe = if cfg!(windows) { "chimera-backend.exe" } else { "chimera-backend" };
    Ok(resource_dir.join("sidecar-dist").join("chimera-backend").join(exe))
}

/// Everything the sidecar needs, resolved from the app once.
///
/// Where this install keeps its data — memory, run receipts, sessions, traces.
///
/// `Settings.home` defaults to the RELATIVE path `.chimera`, which is right for the CLI (data sits
/// beside the project you ran it in) and wrong for a packaged app, which has no meaningful working
/// directory. Without this the sidecar inherited whatever CWD the launcher happened to give it: the
/// install directory under Program Files on Windows (not writable), or a different folder per
/// shortcut — so the same app could show two different histories depending on how it was opened,
/// and a fresh install could fail to write at all.
fn resolve_paths(app: &tauri::App) -> Result<Paths, String> {
    let exe = sidecar_path(app)?;
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|e| format!("cannot create {data_dir:?}: {e}"))?;
    Ok(Paths {
        exe,
        data_dir,
        // Keyed by OUR pid, in temp: two copies of the app share one data dir and would otherwise
        // read each other's port announcement and point their windows at the same backend.
        port_file: std::env::temp_dir()
            .join(format!("chimera-app-port-{}.txt", std::process::id())),
    })
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

/// The address behind `http://host:port`. Parsed by hand rather than pulling in an HTTP client.
fn address_of(url: &str) -> Option<SocketAddr> {
    let hostport = url.strip_prefix("http://").unwrap_or(url);
    hostport.split('/').next()?.to_socket_addrs_first()
}

/// Is anything accepting connections there, right now?
///
/// A TCP connect rather than an HTTP request, and the choice matters in both directions. The kernel
/// completes the handshake from the listen backlog even while the server is busy, so a backend
/// under load is never mistaken for a dead one — a false positive here would kill a working session,
/// which is worse than the outage this whole file is about. What it therefore cannot see is a
/// process whose socket is open and whose event loop is wedged; that failure reaches the user as
/// requests that never answer, and it is the window's own `/api/doctor` poll that notices it. This
/// probe is not a health check and saying otherwise would be the comfortable, false comment.
fn listening(url: &str) -> bool {
    address_of(url)
        .is_some_and(|addr| TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok())
}

/// Wait until the backend's TCP port accepts a connection, so the window doesn't load before it binds.
fn wait_for_listening(url: &str, timeout: Duration) -> Result<(), String> {
    let addr = address_of(url).ok_or("could not resolve backend address")?;
    let start = Instant::now();
    while start.elapsed() < timeout {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok() {
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

/// The label every panel window starts with. The capability file names `main` alone, so a window
/// with this label is granted nothing.
const FLOAT_LABEL: &str = "float-";

/// Labels must be unique for the life of the app; a closed window's label is never reused.
static FLOAT_WINDOWS: AtomicUsize = AtomicUsize::new(0);

/// Whether the page may open `target` as a window of its own: one panel of the screen, drawn apart
/// (dynamic screen, phase 7).
///
/// Without a handler, this runtime refuses every new window — wry marks WebView2's request handled
/// and opens nothing — so this is the only door, and it opens for exactly one address: this backend's
/// origin, the root path, and a single `float` parameter naming a panel. Another site, another port,
/// another path, credentials, a fragment or a second parameter are refused, as everything was before.
///
/// Or a single `conversation` parameter holding a session id: one conversation drawn in a window of its
/// own, so two can be worked at once (the review of several conversations at once, 2026-09-30). The id
/// is held to what the session store keeps: letters, digits, `-` and `_`, at most 64.
///
/// The window it opens is no stronger than the one that asked. It loads the same http origin, and
/// `capabilities/default.json` lists the window `main` alone, so a `float-` window reaches no IPC.
fn is_float_url(target: &tauri::Url, origin: &str) -> bool {
    let Ok(origin) = tauri::Url::parse(origin) else {
        return false;
    };
    let same_origin = target.scheme() == origin.scheme()
        && target.host_str() == origin.host_str()
        && target.port_or_known_default() == origin.port_or_known_default();
    if !same_origin || !target.username().is_empty() || target.password().is_some() {
        return false;
    }
    if target.path() != "/" || target.fragment().is_some() {
        return false;
    }
    let pairs: Vec<(String, String)> = target.query_pairs().into_owned().collect();
    let [(key, value)] = pairs.as_slice() else {
        return false;
    };
    if value.is_empty() || value.len() > 64 {
        return false;
    }
    match key.as_str() {
        // A panel id: lowercase words joined by dots ("activity.jobs"). The page checks it names a real
        // panel; this only keeps the address to the shape one can have.
        "float" => value.chars().all(|c| c.is_ascii_lowercase() || c == '.'),
        // A session id, as the store writes it.
        "conversation" => value.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
        _ => false,
    }
}

/// Whether `target` is a page on the web to hand to the system browser: an external link the page
/// asked to open in a new window (`target="_blank"`, `window.open`).
///
/// Until this, every such link was a silent no-op in the desktop app — the runtime refuses new windows
/// unless told otherwise — while the same links worked in the browser build. The rule is narrow on
/// purpose: `http` or `https`, a host, no credentials in the address, and not this backend's own origin
/// (a page of the app itself is a panel window or nothing, never a browser tab). `file:`, `javascript:`,
/// custom protocols and anything that does not parse stay refused, so a link cannot make the OS run
/// something. The page gains no power from this: it cannot choose the program, only ask for an address,
/// and the address is re-serialised by the URL parser before the OS sees it.
fn is_external_url(target: &tauri::Url, origin: &str) -> bool {
    if !matches!(target.scheme(), "http" | "https") || target.host_str().is_none_or(str::is_empty) {
        return false;
    }
    if !target.username().is_empty() || target.password().is_some() {
        return false;
    }
    let Ok(origin) = tauri::Url::parse(origin) else {
        return false;
    };
    !(target.scheme() == origin.scheme()
        && target.host_str() == origin.host_str()
        && target.port_or_known_default() == origin.port_or_known_default())
}

/// Close every panel window. Called when the main window goes, so the app ends as it did before
/// there were other windows (and the sidecar goes with it), and when the backend moved to another
/// port, which leaves those windows talking to nothing. Their panels return to their docks: which
/// panels float is never stored.
fn close_floats<R: tauri::Runtime>(app: &tauri::AppHandle<R>) {
    for (label, window) in app.webview_windows() {
        if label.starts_with(FLOAT_LABEL) {
            let _ = window.close();
        }
    }
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

/// What went wrong with a backend, and therefore which file the user should be pointed at.
///
/// Two files rather than one, because a crash loop produces both and each answers a different
/// question: the crash report says what the backend was saying when it died, and the startup
/// report says why the replacement could not take its place. Writing both into one path means the
/// second overwrites the answer to the first.
#[derive(Clone, Copy)]
enum Trouble {
    /// It never came up.
    FailedToStart,
    /// It was serving, and then it was not.
    DiedMidSession,
}

impl Trouble {
    fn file(self) -> &'static str {
        match self {
            Self::FailedToStart => "startup-failure.txt",
            Self::DiedMidSession => "backend-crash.txt",
        }
    }

    fn headline(self) -> &'static str {
        match self {
            Self::FailedToStart => "backend failed to start",
            Self::DiedMidSession => "backend stopped while the app was running",
        }
    }
}

/// Write what we know about a broken backend, and return the path.
///
/// Best-effort throughout: this runs on the path where something already went wrong, and a
/// diagnostics writer that can itself fail the startup would be the joke writing itself. Every
/// error here degrades to a less complete file, never to a second failure.
fn write_report(
    data_dir: &Path,
    trouble: Trouble,
    exe: &Path,
    child: &mut Child,
    tail: &Arc<Mutex<VecDeque<String>>>,
    why: &str,
) -> PathBuf {
    let path = data_dir.join(trouble.file());
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
        "Chimera desktop — {}\n\
         version: {}\n\
         backend: {}\n\
         pid: {}\n\
         exit: {exit}\n\
         reason: {why}\n\
         \n\
         --- last {STDERR_KEEP} lines of backend stderr ---\n{}\n",
        trouble.headline(),
        env!("CARGO_PKG_VERSION"),
        exe.display(),
        child.id(),
        if stderr.is_empty() { "<the backend printed nothing>" } else { &stderr },
    );
    let _ = std::fs::create_dir_all(data_dir);
    let _ = std::fs::write(&path, body);
    path
}

/// How long a start may take before it counts as failed.
///
/// A field rather than a constant because the supervisor's own tests drive a backend that never
/// comes up, and a test that waits 45 seconds to prove a timeout is a test nobody runs — which is
/// how the timeout path goes unexercised until a user meets it.
#[derive(Clone, Copy)]
struct Budget {
    url: Duration,
    listen: Duration,
}

impl Default for Budget {
    fn default() -> Self {
        // The frozen exe unpacks and imports the whole agent stack before it binds anything.
        Self { url: Duration::from_secs(45), listen: Duration::from_secs(30) }
    }
}

/// The memo naming the port this install bound last time.
fn memo_path(data_dir: &Path) -> PathBuf {
    data_dir.join("last-port.txt")
}

/// Keep Windows from opening a console window for the backend.
///
/// The two halves of this problem are both deliberate and neither can simply be dropped. The shell
/// is built `windows_subsystem = "windows"`, so it owns no console; the frozen sidecar is built by
/// PyInstaller with `--console`, so it IS a console-subsystem binary — which is what keeps its
/// stdout and stderr real, and the piped stderr above is how a backend that dies on startup gets to
/// say why. Windows resolves that pairing by allocating a NEW console for the child, and the user
/// gets a black terminal sitting beside their app for as long as it runs, printing the port and the
/// cron tick at them.
///
/// `CREATE_NO_WINDOW` is the seam: the child stays a console program with working pipes, and no
/// window is created for it. Running `chimera-backend.exe` by hand still opens a console, which is
/// correct — that one was asked for.
fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    let _ = cmd;
}

/// Launch the backend and wait until it is serving. `wanted` is the port to ask for — see
/// [`remembered_port`] for the first launch, and the supervisor for a restart, where asking for the
/// SAME port is what lets the already-loaded page recover without a reload.
fn start_sidecar(paths: &Paths, wanted: u16, budget: Budget) -> Result<Backend, String> {
    let exe = paths.exe.clone();
    if !exe.exists() {
        return Err(format!("bundled backend not found at {exe:?}"));
    }
    let port_file = paths.port_file.clone();
    let _ = std::fs::remove_file(&port_file);
    let data_dir = paths.data_dir.clone();

    // Asking is all this does: the backend falls back to an OS-assigned free port when the requested
    // one is taken, so a port claimed by something else (or by a second copy of this app) costs a
    // fresh origin once, never a failure to start.
    let memo = memo_path(&data_dir);
    let wanted_arg = wanted.to_string();

    // stderr is PIPED so a backend that dies on startup leaves its last words somewhere. It used to
    // be inherited, which on a windowed Windows build means discarded: the sidecar would print a
    // traceback into a console that does not exist, `start_sidecar` would return a timeout with no
    // detail, and the user would click the icon and get nothing at all — no window, no log, no file.
    //
    // Four competing agent apps each hardened this same step, independently and in four different
    // frameworks. That is not convergent taste; it is the failure everyone met in the field.
    // Where the agent's file tools are rooted when nothing else says. `resolve_app_workspace` falls
    // back to the process CWD, which for a packaged build is wherever the launcher stood — the
    // install directory. Its own docstring names the environment step as the packaged-build answer,
    // and nothing was ever setting it, so the fallback ran: the agent's root became the folder
    // holding the app's own `.env`, and `read_file(".env")` returned the API key in full.
    //
    // A dedicated empty folder, not the home directory and not the install: the backend already
    // argues that case — "an empty, visible, wrong-looking root that the user corrects beats a
    // large, plausible-looking one they never agreed to." Every screen that knows a project still
    // sends it; this is only the answer for the ones that do not.
    let workspace_dir = data_dir.join("workspace");
    let _ = std::fs::create_dir_all(&workspace_dir);

    let mut cmd = Command::new(&exe);
    cmd.args(["--no-open", "--port", &wanted_arg, "--emit-port-file"])
        .arg(&port_file)
        .env("CHIMERA_HOME", data_dir.join("data"))
        .env("CHIMERA_WORKSPACE", &workspace_dir)
        // The tray's switches, so the Settings screen can change them too. The window has no IPC
        // and keeps none (`capabilities/default.json`), so the screen asks the backend, and the
        // backend needs to be told where this process keeps them — it cannot guess: the data dir
        // is a Tauri path, and a backend started by anything else has no shell to talk to.
        .env("CHIMERA_SHELL_PREFS", prefs::prefs_path(&data_dir))
        .env("CHIMERA_SHELL_STATE", prefs::state_path(&data_dir))
        // The backend inherits no stdin, because there is nobody on the other end of it. Inherited,
        // and combined with the CREATE_NO_WINDOW below, Windows hands the child a console with no
        // window: `isatty()` reports a terminal, the host-exec gate believes it, and the first
        // command the agent chose to run blocks on a confirmation prompt drawn where no human can
        // see it — permanently, cancel included. The backend now also declares this for itself, and
        // both halves stay: this one makes the file descriptor tell the truth, that one makes the
        // answer independent of the file descriptor.
        .stdin(Stdio::null())
        .stderr(Stdio::piped());
    hide_console(&mut cmd);
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to launch backend {exe:?}: {e}"))?;

    // Drain it on a thread. Reading it only after a failure would deadlock instead of diagnosing:
    // a pipe nobody reads fills, and a backend blocked writing to a full pipe never reaches the
    // line that would have told us what was wrong.
    let stderr_tail = drain_stderr(&mut child);

    // The frozen exe unpacks + boots; give it a generous window before giving up.
    let url = wait_for_url(&port_file, budget.url)
        .and_then(|url| wait_for_listening(&url, budget.listen).map(|()| url))
        .map_err(|why| {
            // Whatever the backend managed to say, plus the exit code if it already died — written
            // BEFORE this error propagates, because everything above it is about to be unwound.
            let report =
                write_report(&data_dir, Trouble::FailedToStart, &exe, &mut child, &stderr_tail, &why);
            let _ = child.kill();
            let _ = child.wait();
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
    Ok(Backend { child, url, stderr: stderr_tail })
}

/// Consecutive silent looks before a process that is alive and answering nothing is treated as dead.
///
/// One is not enough. A single refused connection can be a moment of exhaustion on a busy machine,
/// and killing a working backend over it would make this feature the cause of the outage it was
/// written to end.
const SILENT_STRIKES: usize = 3;

/// How often the backend is looked at, and how much restarting is worth attempting.
///
/// The window fuse is the one every supervisor has: five restarts inside two minutes, then stop.
/// The second limit exists because the window alone has a hole in it. A backend that HANGS on
/// startup — a config it cannot parse, a port it can never have — fails one budget at a time, and
/// attempts spaced 75 seconds apart never coexist inside a 120-second window. With only the window,
/// such a backend is respawned once a minute for as long as the app is open: an invisible loop
/// replacing a visible failure, which is the trade a fuse exists to refuse.
///
/// The tick is a `try_wait` and one loopback TCP connect — cheap, but it does run for the life of
/// the app, which is why it is two seconds and not two hundred milliseconds.
///
/// A struct rather than constants because the tests below drive every one of these paths, and a
/// suite that waits two minutes to prove a two-minute rule is a suite nobody runs.
#[derive(Clone, Copy)]
struct Tuning {
    tick: Duration,
    budget: Budget,
    max_in_window: usize,
    window: Duration,
    max_consecutive_failures: usize,
}

impl Default for Tuning {
    fn default() -> Self {
        Self {
            tick: Duration::from_secs(2),
            budget: Budget::default(),
            max_in_window: 5,
            window: Duration::from_secs(120),
            max_consecutive_failures: 3,
        }
    }
}

/// The restart budget.
///
/// Kept apart from the loop that spends it because it is the entire safety argument, and because
/// arithmetic over a sliding window is the part that can be wrong while everything around it looks
/// right.
struct Fuse {
    window: Duration,
    max_in_window: usize,
    max_consecutive_failures: usize,
    recent: VecDeque<Instant>,
    failures: usize,
    spent: usize,
}

impl Fuse {
    fn new(tuning: &Tuning) -> Self {
        Self {
            window: tuning.window,
            max_in_window: tuning.max_in_window,
            max_consecutive_failures: tuning.max_consecutive_failures,
            recent: VecDeque::new(),
            failures: 0,
            spent: 0,
        }
    }

    /// May the backend be restarted right now? Records the attempt when the answer is yes.
    fn allows(&mut self, now: Instant) -> bool {
        while self.recent.front().is_some_and(|t| now.duration_since(*t) > self.window) {
            self.recent.pop_front();
        }
        if self.recent.len() >= self.max_in_window || self.failures >= self.max_consecutive_failures
        {
            return false;
        }
        self.recent.push_back(now);
        self.spent += 1;
        true
    }

    /// How the attempt just allowed turned out. A start that worked clears the consecutive count:
    /// a backend that dies once an hour is not a backend that cannot start.
    fn attempt_ended(&mut self, started: bool) {
        self.failures = if started { 0 } else { self.failures + 1 };
    }
}

/// What one look at the backend can tell us.
#[derive(Debug, PartialEq, Eq)]
enum Health {
    /// Running, and something accepts connections on its port.
    Serving,
    /// The process is gone.
    Exited,
    /// The process is alive and its port answers nothing.
    Silent,
}

fn look(backend: &mut Backend) -> Health {
    match backend.child.try_wait() {
        Ok(Some(_)) => Health::Exited,
        Ok(None) if listening(&backend.url) => Health::Serving,
        Ok(None) => Health::Silent,
        // We could not ask. Assume it is fine: this branch is a broken handle, not a broken
        // backend, and a restart we cannot justify ends a session that was working.
        Err(_) => Health::Serving,
    }
}

/// What one turn of the supervisor's loop found in the slot the backend lives in.
///
/// `Missing` is its own case and that is the point: an empty slot means either that the app took
/// the backend on its way out or that the previous restart failed and there is nothing there YET.
/// Reading both as "the app is quitting" ends the supervision at the exact moment it is needed —
/// which is what the first version of this loop did, and what its own test caught.
enum Found {
    Alive,
    Missing,
    JustDied(Backend),
}

/// Something the supervisor did that the app has to act on.
enum Supervised {
    /// The backend is back, at this URL — usually the one it had, because a restart asks for the
    /// port the window is already pointing at. When it is NOT the same, the page in the window is
    /// talking to an origin that no longer exists and has to be moved there.
    Restarted(String),
    /// Out of budget; nothing further will be tried. The message says what happened, what to do,
    /// and which file holds the backend's own words.
    GaveUp(String),
}

/// The message shown when the supervisor stops trying.
///
/// It names a file on purpose. A give-up with nothing to open is the "nothing happens" failure
/// again, one level up: the user is told the app is broken and given no way to find out why.
fn gave_up_message(
    d: &Dialogo,
    fuse: &Fuse,
    report: Option<&Path>,
    last_error: Option<&str>,
) -> String {
    // The language arrives as a PARAMETER rather than from `dialogo()`, and that is what keeps the
    // supervisor's tests deterministic: they drive `supervise` end to end and read the announced
    // string, so a message built from the machine's locale would assert in English on the CI runner
    // and in Portuguese on the machine that wrote it. The tests pass `&DIALOGO[0]` explicitly.
    let mut message = d.parou.replace("{n}", &fuse.spent.to_string());
    if let Some(path) = report {
        // The path itself is not translated — it is a path.
        message.push_str(&format!("\n\n{}\n{}", d.relatorio, path.display()));
    }
    if let Some(why) = last_error {
        // Neither is this: it is the OS's or the backend's own words, and it is what a bug report
        // needs to be searchable.
        message.push_str(&format!("\n\n{} {why}", d.ultima_tentativa));
    }
    message
}

/// Watch the backend, and bring it back when it dies.
///
/// This is the whole of the "it comes back on its own" half. Without it, a backend that died
/// mid-session left the window loaded and every panel in it showing a "Try again" that could only
/// ever fail, and the only fix was for the user to guess they should close and reopen the app.
///
/// Three things it is careful about, each of which is a way to make the situation worse:
///
///   1. **It reads the corpse before replacing it.** The dead process's stderr is the only
///      diagnosis anyone has, and it exists only until the process is dropped.
///   2. **It asks for the same port.** The window's origin — and, browsers being what they are, the
///      `localStorage` behind it — is `http://127.0.0.1:<port>`. A backend that comes back
///      somewhere else leaves the loaded page talking to nothing at all.
///   3. **It gives up.** See [`Tuning`].
fn supervise(
    state: Arc<Sidecar>,
    paths: Paths,
    tuning: Tuning,
    // The language the give-up message is written in. A parameter and not `dialogo()` — see the
    // note on `gave_up_message`. (A line comment: Rust does not take doc comments on parameters.)
    d: &'static Dialogo,
    mut announce: impl FnMut(Supervised),
) {
    let mut fuse = Fuse::new(&tuning);
    let mut silent = 0usize;
    // The port to ask for. Carried across failed attempts so a backend that takes three tries to
    // come up still comes up where the window is looking.
    let mut wanted: u16 = 0;
    let mut last_report: Option<PathBuf> = None;
    let mut last_error: Option<String> = None;

    loop {
        std::thread::sleep(tuning.tick);
        if state.stopping.load(Ordering::SeqCst) {
            return;
        }

        // Looking and taking the corpse out happen in ONE critical section, so a quit that lands
        // between them cannot find a backend that is neither owned by the app nor killed.
        let found = {
            let Ok(mut guard) = state.backend.lock() else { return };
            match guard.as_mut() {
                // `kill_sidecar` sets `stopping` BEFORE it takes the backend, so a flag read here
                // cannot miss a quit that has already happened. Anything else empty is our own gap
                // between a failed restart and the next attempt — see `Found`.
                None if state.stopping.load(Ordering::SeqCst) => return,
                None => Found::Missing,
                Some(backend) => match look(backend) {
                    Health::Serving => {
                        silent = 0;
                        Found::Alive
                    }
                    Health::Silent => {
                        silent += 1;
                        if silent < SILENT_STRIKES {
                            Found::Alive
                        } else {
                            silent = 0;
                            let _ = backend.child.kill();
                            guard.take().map_or(Found::Missing, Found::JustDied)
                        }
                    }
                    Health::Exited => {
                        silent = 0;
                        guard.take().map_or(Found::Missing, Found::JustDied)
                    }
                },
            }
        };

        if let Found::JustDied(mut dead) = found {
            // The last words, read before anything replaces the process that said them. A respawn
            // that skipped this would delete the only account of why the backend keeps dying — and
            // a restart loop with no record is indistinguishable from an app that is merely slow.
            wanted = port_of(&dead.url).unwrap_or(wanted);
            // Reap first, then diagnose: it leaves no zombie behind, and it means the report shows
            // the real exit status instead of "still running" for a process this loop killed one
            // line earlier — a sentence that would be false in exactly the file somebody opens to
            // find out what happened.
            let _ = dead.child.wait();
            last_report = Some(write_report(
                &paths.data_dir,
                Trouble::DiedMidSession,
                &paths.exe,
                &mut dead.child,
                &dead.stderr,
                "the backend stopped answering while the app was running",
            ));
        } else if let Found::Alive = found {
            continue;
        }

        if !fuse.allows(Instant::now()) {
            announce(Supervised::GaveUp(gave_up_message(
                d,
                &fuse,
                last_report.as_deref(),
                last_error.as_deref(),
            )));
            return;
        }

        match start_sidecar(&paths, wanted, tuning.budget) {
            Ok(mut fresh) => {
                fuse.attempt_ended(true);
                wanted = port_of(&fresh.url).unwrap_or(wanted);
                let url = fresh.url.clone();
                {
                    // Storing it and checking `stopping` are one critical section for the reason
                    // given on `Sidecar`: quitting during a restart must not leave the replacement
                    // running after the window that owned it is gone.
                    let Ok(mut guard) = state.backend.lock() else { return };
                    if state.stopping.load(Ordering::SeqCst) {
                        let _ = fresh.child.kill();
                        return;
                    }
                    *guard = Some(fresh);
                }
                last_error = None;
                announce(Supervised::Restarted(url));
            }
            Err(why) => {
                // `start_sidecar` has already written its own report and named it inside `why`.
                fuse.attempt_ended(false);
                last_error = Some(why);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{
        deve_perguntar, drain_stderr, idioma_do_dialogo, look, port_of, remembered_port,
        start_sidecar, supervise, write_report, Backend, Budget, Dialogo, Fuse, Health, Paths,
        Sidecar, Supervised, Trouble, Tuning, DIALOGO, INTERVALO_DE_CHECAGEM, STDERR_KEEP,
    };
    use std::collections::VecDeque;

    /// This file, minus its own test module.
    ///
    /// TWO TRAPS live here, both the shape `the_sidecar_actually_pipes_its_stderr` documents.
    ///
    /// 1. `mod tests` sits in the MIDDLE of the file — `check_for_update` is defined after it — so
    ///    "everything before `#[cfg(test)]`" is not the production code, it is the first half of
    ///    it. A window that stopped there made one assert panic and another pass VACUOUSLY, over a
    ///    region that could not contain what it was looking for. A test that cannot fail is worse
    ///    than no test.
    /// 2. Line endings are normalised FIRST. The file is checked out CRLF on Windows and LF on the
    ///    Linux runner, so a window anchored on "\n}\n" matches on one and silently never matches
    ///    on the other — green on CI and red on the machine that wrote it. It was that way round.
    fn producao() -> String {
        let fonte = include_str!("main.rs").replace("\r\n", "\n");
        let (antes, resto) = fonte
            .split_once("#[cfg(test)]")
            .expect("o modulo de testes marca onde a producao e' interrompida");
        // The module's closing brace is the first `}` at column zero after it; every brace inside
        // is indented. Production resumes after that.
        let depois = resto.split_once("\n}\n").map_or("", |(_, d)| d);
        format!("{antes}{depois}")
    }

    /// A second launch hands over to the running app, and it does so before anything else: the
    /// single-instance plugin is the first one registered, and what it does is bring the main
    /// window forward. Without it a second click started a second backend on the same data folder,
    /// which the backend now refuses — so the second click ended in an error dialog.
    #[test]
    fn a_second_launch_hands_over_before_anything_else_runs() {
        let fonte = producao();
        let (_, main) = fonte.split_once("fn main() {").expect("main exists");
        let primeiro = main
            .find(".plugin(")
            .expect("main registers plugins");
        assert!(
            main[primeiro..].starts_with(".plugin(tauri_plugin_single_instance::init("),
            "the single-instance plugin is not the first one registered"
        );
        let (_, trazer) = fonte.split_once("fn bring_forward(").expect("bring_forward exists");
        let corpo = trazer.split_once("\n}\n").map_or("", |(c, _)| c);
        for passo in ["get_webview_window(\"main\")", "unminimize()", "show()", "set_focus()"] {
            assert!(corpo.contains(passo), "bring_forward no longer calls {passo}");
        }
    }

    use std::net::TcpListener;
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, Stdio};
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

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

        let path = write_report(
            &dir,
            Trouble::FailedToStart,
            Path::new("chimera-backend"),
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
        let path =
            write_report(&dir, Trouble::FailedToStart, Path::new("x"), &mut child, &tail, "why");
        let body = std::fs::read_to_string(&path).unwrap();
        assert!(
            body.contains("the backend printed nothing"),
            "an empty tail must say so rather than leave a blank section: {body}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The two tests above are HELPER tests, and I found that out by trying to break them.
    ///
    /// Removing `.stderr(Stdio::piped())` from `start_sidecar` left them green, because each spawns
    /// its own child and pipes it explicitly — they prove `drain_stderr` and `write_report` work,
    /// and say nothing about whether the sidecar path calls them. That is the same "tests the class,
    /// not the wiring" defect this project has a skill card about.
    ///
    /// The note that used to live here said the behavioural version would mean restructuring
    /// `start_sidecar` to take a path instead of `&tauri::App`, and that it was worth doing. It has
    /// been done: `a_dead_backend_is_started_again_without_the_user_doing_anything` and its
    /// neighbours below call the real function against a stand-in backend. This stays anyway,
    /// because it is a one-line guard on the exact regression and it costs nothing.
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
            spawn.contains("write_report"),
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

    /// Every language has all four strings, and none of them is empty.
    ///
    /// An empty button label renders as a button with nothing on it — you would not know which one
    /// installs and which one postpones, on the dialog that asks to change your machine.
    /// Every string in one language, paired with its field name. One list, so the tests below and
    /// `nenhum_campo_do_dialogo_fica_sem_teste` are talking about the same set.
    fn campos(d: &'static Dialogo) -> Vec<(&'static str, &'static str)> {
        vec![
            ("menu", d.menu),
            ("titulo", d.titulo),
            ("mensagem", d.mensagem),
            ("atualizar", d.atualizar),
            ("depois", d.depois),
            ("atual", d.atual),
            ("falhou", d.falhou),
            ("sair", d.sair),
            ("nao_iniciou_titulo", d.nao_iniciou_titulo),
            ("nao_iniciou", d.nao_iniciou),
            ("parou_titulo", d.parou_titulo),
            ("parou", d.parou),
            ("relatorio", d.relatorio),
            ("ultima_tentativa", d.ultima_tentativa),
            ("abrir", d.abrir),
            ("manter_na_bandeja", d.manter_na_bandeja),
            ("iniciar_com_sistema", d.iniciar_com_sistema),
            ("chamar_atencao", d.chamar_atencao),
            ("atalho", d.atalho),
            ("atalho_falhou", d.atalho_falhou),
            ("autostart_falhou", d.autostart_falhou),
            ("prefs_falhou", d.prefs_falhou),
            ("prefs_guardado", d.prefs_guardado),
            ("sem_acesso", d.sem_acesso),
            ("dica_rodando", d.dica_rodando),
            ("dica_gasto", d.dica_gasto),
            ("dica_aguardando", d.dica_aguardando),
        ]
    }

    /// Every language has every string, and none of them is empty.
    ///
    /// An empty button label renders as a button with nothing on it — you would not know which one
    /// installs and which one postpones, on the dialog that asks to change your machine.
    #[test]
    fn cada_idioma_tem_o_dialogo_inteiro() {
        for d in &DIALOGO {
            for (campo, texto) in campos(d) {
                assert!(!texto.trim().is_empty(), "{}: {campo} vazio", d.codigo);
            }
        }
    }

    /// No field of `Dialogo` escapes the tests by being added and not listed.
    ///
    /// `campos()` is written by hand because Rust has no reflection here, and a hand-written list
    /// is exactly the kind that stops covering a field somebody adds next month. So the list is
    /// checked against the struct definition, read out of this very file: adding a fourteenth
    /// string and forgetting it here fails, rather than shipping untested in ten languages.
    #[test]
    fn nenhum_campo_do_dialogo_fica_sem_teste() {
        let fonte = producao();
        let corpo = fonte
            .split_once("struct Dialogo {")
            .expect("a struct existe")
            .1
            .split_once("\n}")
            .expect("a struct fecha")
            .0;
        let na_struct: Vec<&str> = corpo
            .lines()
            .filter_map(|l| l.trim().strip_suffix(": &'static str,"))
            .filter(|nome| *nome != "codigo")
            .collect();
        let na_lista: Vec<&str> = campos(&DIALOGO[0]).into_iter().map(|(n, _)| n).collect();
        assert_eq!(
            na_struct, na_lista,
            "campos(): a lista dos testes saiu de sincronia com a struct Dialogo"
        );
    }

    /// The version placeholder survives translation.
    ///
    /// The template is a lookup, not a literal, so `format!` cannot check it and a translation that
    /// dropped `{v}` would ship a dialog that never names the version it is offering. The frontend
    /// has the same test for the same reason (`i18n.test.tsx`, "keeps every {placeholder} intact").
    #[test]
    fn dialogo_mantem_os_marcadores() {
        for d in &DIALOGO {
            for (campo, texto, marcador) in [
                ("mensagem", d.mensagem, "{v}"),
                ("atual", d.atual, "{v}"),
                ("falhou", d.falhou, "{e}"),
                ("parou", d.parou, "{n}"),
                ("atalho", d.atalho, "{k}"),
                ("atalho_falhou", d.atalho_falhou, "{k}"),
                ("atalho_falhou", d.atalho_falhou, "{e}"),
                ("autostart_falhou", d.autostart_falhou, "{e}"),
                ("prefs_falhou", d.prefs_falhou, "{f}"),
                ("prefs_falhou", d.prefs_falhou, "{e}"),
                ("prefs_guardado", d.prefs_guardado, "{f}"),
                ("sem_acesso", d.sem_acesso, "{e}"),
                ("dica_rodando", d.dica_rodando, "{n}"),
                ("dica_gasto", d.dica_gasto, "{usd}"),
                ("dica_aguardando", d.dica_aguardando, "{n}"),
            ] {
                assert!(
                    texto.contains(marcador),
                    "{}: {campo} perdeu o {marcador} e nunca vai dizer qual",
                    d.codigo
                );
            }
        }
        // And it is really substituted, not merely present.
        let pronta = idioma_do_dialogo(Some("pt-BR")).mensagem.replace("{v}", "0.49.0");
        assert!(pronta.contains("0.49.0") && !pronta.contains("{v}"));
    }

    /// The ten languages here are the ten the app ships — read out of the frontend, not copied.
    ///
    /// Two lists of one fact in two languages that no compiler relates. Adding an eleventh language
    /// to the app and not here would leave its users an English dialog, and nothing would say so.
    #[test]
    fn os_idiomas_sao_os_mesmos_do_aplicativo() {
        let fonte = include_str!("../../src/lib/i18n.tsx");
        let lista = fonte
            .split_once("export const LANGS = [")
            .expect("LANGS existe em i18n.tsx")
            .1
            .split_once("] as const;")
            .expect("LANGS fecha")
            .0;
        let do_app: Vec<&str> = lista
            .split("code: \"")
            .skip(1)
            .filter_map(|resto| resto.split_once('"').map(|(codigo, _)| codigo))
            .collect();
        let daqui: Vec<&str> = DIALOGO.iter().map(|d| d.codigo).collect();
        assert_eq!(
            do_app, daqui,
            "a lista de idiomas do dialogo saiu de sincronia com a do aplicativo"
        );
    }

    /// A locale tag becomes one of the ten, or English.
    #[test]
    fn o_locale_do_sistema_escolhe_o_idioma() {
        let codigo = |tag: Option<&str>| idioma_do_dialogo(tag).codigo;

        // A region suffix must not defeat the match — nobody's locale is a bare "pt".
        assert_eq!(codigo(Some("pt-BR")), "pt");
        assert_eq!(codigo(Some("pt_PT")), "pt"); // macOS uses an underscore
        assert_eq!(codigo(Some("zh-Hans-CN")), "zh");
        assert_eq!(codigo(Some("PT-br")), "pt"); // and it is case-insensitive
        assert_eq!(codigo(Some("de")), "de");

        // Anything unknown lands on English rather than on nothing.
        assert_eq!(codigo(Some("sv-SE")), "en");
        assert_eq!(codigo(Some("")), "en");
        assert_eq!(codigo(None), "en");
    }

    /// The dialog is NOT still the hardcoded English it used to be.
    ///
    /// The control for the four tests above: they all pass against a `check_for_update` that builds
    /// the table correctly and then ignores it. This one reads the call site.
    ///
    /// TWO TRAPS, both hit while writing it, both the same shape as the one
    /// `the_sidecar_actually_pipes_its_stderr` documents above.
    ///
    /// 1. `mod tests` sits in the MIDDLE of this file — `check_for_update` is defined after it — so
    ///    "everything before `#[cfg(test)]`" is not the production code, it is the first half of it.
    ///    The positive assert panicked; the negative one passed VACUOUSLY, searching a region that
    ///    could not contain what it was looking for. A test that cannot fail is worse than none.
    /// 2. The needles are assembled with `concat!` so this test's own source never contains the
    ///    strings it searches for. Spelled out, the search window would find its own answer.
    #[test]
    fn o_dialogo_usa_a_tabela_e_nao_um_literal() {
        let producao = producao();

        let assinatura = concat!("async fn ", "check_for_update");
        let corpo = producao
            .split_once(assinatura)
            .expect("check_for_update existe fora do modulo de testes")
            .1;

        // COUNTED, not merely present — and that distinction was earned twice.
        //
        // First version: checked the button and the table lookup, and a sabotage that replaced
        // `.title(...)` with the old English literal went through. The test named the right
        // property and covered a quarter of it.
        //
        // Second version: one `contains` per string. Also went through, for a subtler reason —
        // production now opens THREE dialogs (already-up-to-date, the update itself, and the tray's
        // failure), so `contains(".title(d.titulo)")` is satisfied by any one of them while the
        // other two say whatever they like. A presence check over repeated call sites can only ever
        // prove that at least one is right.
        // Scoped to the updater, and only it. The first counting version swept all of production
        // and read 6 `.title(` against 3 from the table — the other three are the backend-failure
        // dialogs, which are a different feature and are still English. A rule that fails correct
        // code is as useless as one that passes broken code.
        let ate_o_fim_da_fn = corpo.split_once("\nfn ").map_or(corpo, |(cabeca, _)| cabeca);
        let braco = producao
            .split_once(concat!("\"update\" =", ">"))
            .and_then(|(_, resto)| resto.split_once("_ => {}"))
            .map_or(String::new(), |(arm, _)| arm.to_string());
        let regiao = format!("{ate_o_fim_da_fn}{braco}");

        for (chamada, da_tabela) in [(".title(", ".title(d."), (".message(", ".message(d.")] {
            let todas = regiao.matches(chamada).count();
            let certas = regiao.matches(da_tabela).count();
            assert!(todas > 0, "sumiram as chamadas {chamada} — o teste parou de medir algo");
            assert_eq!(
                todas, certas,
                "{} de {todas} chamadas {chamada} do updater nao vem da tabela de idiomas",
                todas - certas
            );
        }

        // The buttons have one call site, so presence is enough for them.
        for (parte, agulha) in [
            ("o idioma", concat!("let d = dial", "ogo();")),
            ("o botao de atualizar", "d.atualizar.to_string()"),
            ("o botao de adiar", "d.depois.to_string()"),
        ] {
            assert!(
                corpo.contains(agulha),
                "{parte} do dialogo nao vem mais da tabela de idiomas"
            );
        }

        // There is no "and the old English literals are gone" assertion, and the reason is worth
        // keeping: one was written, and it failed on CORRECT code. "Chimera update available" is
        // still in this file — as the English row of the table, which is exactly where it belongs.
        // A rule phrased over the string could not tell the translation from the hardcoding; the
        // five above are phrased over the CALL SITE, which is the thing that can be wrong.
        //
        // It was caught late for a method reason worth writing down too: the sabotages were run
        // without re-running the suite clean afterwards, so "it fails when broken" was established
        // and "it passes when correct" was assumed. Both directions, every time.
    }

    /// The tray offers a way to ASK, and its label comes from the table like everything else.
    ///
    /// Without this item the only update check is the one at startup, which says nothing when
    /// there is nothing — so a user who wanted to know had to relaunch and hope. The label is the
    /// one string of the four that is NOT in a dialog, and it would be the easy one to hardcode.
    #[test]
    fn a_bandeja_oferece_verificar_atualizacoes() {
        let producao = producao();
        let menu = producao
            .split_once(concat!("let atualizar = MenuItem", "::with_id"))
            .expect("o item de atualizar existe na bandeja")
            .1;
        assert!(
            menu.contains(concat!("dialogo()", ".menu")),
            "o rotulo do item de bandeja parou de vir da tabela de idiomas"
        );
        // Read over the LIST the menu is built from, not over one spelling of it. This used to be
        // `contains("&[&atualizar, &quit]")`, which the tray switches of study 29 broke by adding
        // items to the same list — the property ("the update item is in the menu") still held, the
        // literal did not. Bounded at the list's own `]`, so an item mentioned elsewhere cannot pass.
        let lista = producao
            .split_once(concat!("Menu::with_", "items(\n"))
            .or_else(|| producao.split_once(concat!("Menu::with_", "items(")))
            .expect("o menu da bandeja e' montado de uma lista")
            .1
            .split_once("],")
            .expect("a lista fecha")
            .0;
        for item in [concat!("&atual", "izar,"), concat!("&qu", "it,")] {
            assert!(lista.contains(item), "o item {item} saiu do menu da bandeja: {lista}");
        }
        assert!(
            producao.contains(concat!("\"update\" =", ">")),
            "o clique no item nao e' mais tratado"
        );
    }

    /// Only a check the user asked for says "you are already up to date".
    ///
    /// The two paths differ in exactly one way and it is the whole design: at startup, silence is
    /// correct — an app that announces "nothing to do" every launch is nagging. After a click,
    /// silence is a bug: a menu item that does nothing visible reads as broken, and the next move
    /// is to click it again.
    ///
    /// Read from the source because the alternative is booting Tauri, opening a tray and clicking
    /// it. This pins the branch and the two call sites, which is where the distinction lives.
    #[test]
    fn so_a_verificacao_pedida_diz_que_ja_esta_atualizado() {
        let producao = producao();
        let corpo = producao
            .split_once(concat!("async fn ", "check_for_update"))
            .expect("check_for_update existe fora do modulo de testes")
            .1;
        let ate_o_update = corpo
            .split_once("let confirmed")
            .expect("o ramo do 'ja atualizado' vem antes do dialogo de update")
            .0;
        assert!(
            ate_o_update.contains("if pedida {"),
            "o aviso de 'ja esta atualizado' deixou de ser condicional — o arranque voltou a avisar"
        );
        assert!(
            ate_o_update.contains(concat!("d.atu", "al.replace")),
            "o 'ja esta atualizado' nao vem mais da tabela de idiomas"
        );
        // E os dois chamadores dizem coisas diferentes, que e' o que faz o ramo significar algo.
        assert!(
            producao.contains(concat!("check_for_update(handle.clone(), fal", "se)")),
            "a checagem do arranque deixou de ser silenciosa"
        );
        assert!(
            producao.contains(concat!("check_for_update(handle.clone(), tr", "ue)")),
            "a checagem da bandeja deixou de se anunciar como pedida"
        );
    }

    /// The backend-failure dialogs and the tray's Quit come from the table too.
    ///
    /// These were left English when the updater was translated, and named in the changelog so they
    /// would be found rather than discovered. This is that debt paid, and pinned: three dialogs and
    /// a menu item, each one a place where a literal is the easy thing to write.
    ///
    /// What is deliberately NOT translated, and must stay that way: the detail that follows
    /// `nao_iniciou`, the report path, and the last restart error. They are a path, an OS error and
    /// the backend's own stderr — they go into a bug report, and a translated system error is one
    /// nobody can search for.
    #[test]
    fn os_dialogos_de_falha_tambem_falam_a_lingua_do_usuario() {
        let producao = producao();
        for (o_que, agulha) in [
            ("o titulo do 'nao iniciou'", ".title(d.nao_iniciou_titulo)"),
            ("o corpo do 'nao iniciou'", concat!("d.nao_iniciou", ")")),
            ("o titulo do 'backend parou'", concat!("dialogo().parou_", "titulo")),
            ("o item Quit da bandeja", concat!("dialogo()", ".sair")),
            ("a frase do 'nao consegui trazer de volta'", concat!("d.parou.rep", "lace(\"{n}\"")),
            ("o rotulo do relatorio", "d.relatorio"),
            ("o rotulo da ultima tentativa", "d.ultima_tentativa"),
        ] {
            assert!(
                producao.contains(agulha),
                "{o_que} nao vem mais da tabela de idiomas"
            );
        }

        // And the diagnostics are still passed through verbatim, not wrapped in a translation.
        assert!(
            producao.contains("path.display()") && producao.contains("{why}"),
            "o diagnostico tecnico parou de ser repassado verbatim — ele e' o que vai no bug report"
        );
    }

    /// The tray's label and the label the app's own panel tells you to look for are the same text.
    ///
    /// They live in two tables in two languages: `Dialogo.menu` here, and `update.trayItem` in
    /// `i18n.tsx`. The panel that appears when a new version is found now says "update it from the
    /// tray menu — «Verificar atualizações»", so a drift between them would send somebody hunting
    /// for a menu item spelled differently from the menu item. Pointing at the wrong name is worse
    /// than pointing at nothing, because the person concludes the feature is missing.
    ///
    /// Nothing else can catch this: one side is Rust and the other is TypeScript, they are shipped
    /// in the same binary, and no compiler sees both.
    #[test]
    fn o_rotulo_da_bandeja_e_o_mesmo_dos_dois_lados() {
        let fonte = include_str!("../../src/lib/i18n.tsx");

        // Matched by LANGUAGE CODE, never by position — and that is not caution, it is a bug this
        // test hit on its first run. The dictionaries are DEFINED in the file as en, pt, es, fr,
        // de, zh, ja, it, pl, ru; `LANGS` (and so `DIALOGO`) orders them en, pt, es, fr, de, it,
        // pl, zh, ja, ru. Zipping the two lists compared Italian against Chinese and reported a
        // drift that did not exist. `os_idiomas_sao_os_mesmos_do_aplicativo` cannot catch this: it
        // reads `LANGS`, which agrees, while the definitions below it do not.
        let mut conferidos = 0;
        for d in &DIALOGO {
            let cabeca = format!("const {}: Dict = {{", d.codigo);
            let dicionario = fonte
                .split_once(cabeca.as_str())
                .unwrap_or_else(|| panic!("o dicionario {} existe em i18n.tsx", d.codigo))
                .1;
            // Bounded at the next dictionary, so a missing key here cannot silently read the next
            // language's value and pass.
            let dicionario = dicionario.split_once("\n};").map_or(dicionario, |(head, _)| head);
            let no_app = dicionario
                .split_once("\"update.trayItem\": \"")
                .unwrap_or_else(|| panic!("{}: nao tem update.trayItem", d.codigo))
                .1
                .split_once('"')
                .expect("a string fecha")
                .0;
            assert_eq!(
                d.menu, no_app,
                "{}: o rotulo da bandeja no Rust e no i18n.tsx divergiram",
                d.codigo
            );
            conferidos += 1;
        }
        assert_eq!(conferidos, DIALOGO.len(), "algum idioma nao foi conferido");
    }

    /// Declining an update silences the AUTOMATIC rounds, and nothing else.
    ///
    /// This is the whole rule of the periodic check, and it is a function so it can be asserted
    /// rather than read. Without it, looking every six hours means asking every six hours — the
    /// nagging the automatic path has always avoided, arriving through the front door.
    #[test]
    fn dizer_depois_cala_as_rondas_automaticas_e_nada_mais() {
        // Nothing declined yet: ask.
        assert!(deve_perguntar(false, "0.49.2", None));

        // Declined THIS version, on an automatic round: stay quiet.
        assert!(!deve_perguntar(false, "0.49.2", Some("0.49.2")));

        // A NEWER version is a different string, so it asks again — the property falls out of
        // comparing rather than needing a rule of its own, and this is what pins that.
        assert!(deve_perguntar(false, "0.49.3", Some("0.49.2")));

        // And a check the user ASKED for always asks. Answering "you already declined that one" to
        // somebody who just clicked the menu item would be refusing what they requested.
        assert!(deve_perguntar(true, "0.49.2", Some("0.49.2")));

        // And the rule is WIRED, not merely correct. Deleting the line that REMEMBERS the decision
        // leaves every assertion above passing — a pure function nobody feeds is a rule that holds
        // in the abstract while the dialog asks again in six hours. The sabotage that found this
        // gap removed exactly that line.
        let producao = producao();
        assert!(
            producao.contains(concat!("deve_perg", "untar(pedida, &update.version")),
            "check_for_update parou de consultar a regra antes de abrir o dialogo"
        );
        assert!(
            producao.contains(concat!("*adiada = Some(update.", "version.clone())")),
            "dizer 'depois' nao guarda mais a versao, entao a proxima ronda pergunta de novo"
        );
    }

    /// The check is not launch-only, which is the defect this all exists for.
    ///
    /// Read from the source because the alternative is waiting six hours. What it pins is the shape
    /// that was missing: a loop, a sleep of the declared interval, and a call inside it. A single
    /// call at startup satisfied every other test in this file.
    #[test]
    fn um_app_aberto_o_dia_todo_volta_a_olhar() {
        let producao = producao();
        // TWO wrong windows before this one, both mine, both the same shape — a needle that finds
        // something other than what it names:
        //
        //   1. `std::thread::spawn(move || {` matches the SUPERVISOR first, which also runs on a
        //      thread and comes earlier in the file. That window reported the update check as
        //      launch-only while the loop sat twenty lines below it, untouched.
        //   2. `split_once` CONSUMES the delimiter, so anchoring on the startup call removed that
        //      call from the window and the count came out one instead of two.
        //
        // Sliced by index, delimiter kept: from the `spawn` that precedes this thread's own first
        // call, to the `});` that closes it.
        let marca = concat!("block_on(check_for_", "update(handle.clone(), false))");
        let em = producao
            .find(marca)
            .expect("o arranque ainda dispara a checagem numa thread");
        let inicio = producao[..em].rfind("std::thread::spawn").unwrap_or(0);
        let fim = producao[em..].find("});").map_or(producao.len(), |i| em + i);
        let bloco = &producao[inicio..fim];

        assert!(bloco.contains("loop {"), "a checagem voltou a ser so' no arranque");
        assert!(
            bloco.contains("INTERVALO_DE_CHECAGEM"),
            "o laco nao dorme o intervalo declarado"
        );
        assert_eq!(
            bloco.matches(concat!("check_for_", "update(")).count(),
            2,
            "esperava a checagem do arranque MAIS a do laco — uma so' e' o defeito de volta"
        );
    }

    /// Six hours, not six minutes.
    ///
    /// The interval is the difference between a background check and a poll. Nothing enforces a
    /// sane number, and a stray `from_secs(6)` reads almost the same on the page.
    #[test]
    fn o_intervalo_e_de_horas() {
        assert!(
            INTERVALO_DE_CHECAGEM >= std::time::Duration::from_secs(60 * 60),
            "checar mais de uma vez por hora e' polling, nao verificacao de fundo"
        );
        assert!(
            INTERVALO_DE_CHECAGEM <= std::time::Duration::from_secs(24 * 60 * 60),
            "mais de um dia entre checagens devolve o defeito para quem deixa o app aberto"
        );
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
    // ---------------------------------------------------------------------------------------
    // The supervisor.
    //
    // These run the real `start_sidecar` and the real `supervise` against a stand-in backend, which
    // is what the note above promised and could not do while the function needed a `tauri::App`.
    // ---------------------------------------------------------------------------------------

    /// Timings for a test. Small, but not so small that a loaded CI runner reads a slow spawn as a
    /// failure — the budget only costs time when a start is EXPECTED to fail, and the tests that
    /// expect that shorten it themselves.
    fn quick(tuning: Tuning) -> Tuning {
        Tuning { tick: Duration::from_millis(50), ..tuning }
    }

    static SCRATCH: AtomicUsize = AtomicUsize::new(0);

    /// A private directory for one test. Unique per call, because cargo runs these in parallel
    /// threads of ONE process and a shared path is a silent cross-test overwrite.
    fn scratch(name: &str) -> PathBuf {
        let n = SCRATCH.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("chimera-{name}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a scratch directory");
        dir
    }

    /// A stand-in for the frozen Python backend.
    ///
    /// The real one is a PyInstaller build produced by another job and cannot be a test fixture.
    /// That is not a compromise for what these tests ask: every one of them is about what the SHELL
    /// does when the process on the other end appears, dies, or never arrives.
    struct Fake<'a> {
        dir: &'a Path,
        /// The URL it announces into the `--emit-port-file` path, which is argument 5.
        url: &'a str,
        /// Whether it stays alive after announcing, or exits the moment it has.
        lingers: bool,
        /// While this file exists it refuses to come up: no announcement, non-zero exit.
        breaks_when: Option<&'a Path>,
        /// A line appended on every run, so a test can count spawns without asking the supervisor
        /// how many it thinks it made.
        tally: Option<&'a Path>,
    }

    impl Fake<'_> {
        fn write(self) -> PathBuf {
            let path = self.dir.join(if cfg!(windows) { "backend.cmd" } else { "backend.sh" });
            let mut body = String::new();
            if cfg!(windows) {
                body.push_str("@echo off\r\n");
                if let Some(tally) = self.tally {
                    body.push_str(&format!(">>\"{}\" echo ran\r\n", tally.display()));
                }
                body.push_str("echo fake backend speaking 1>&2\r\n");
                if let Some(marker) = self.breaks_when {
                    body.push_str(&format!("if exist \"{}\" exit /b 9\r\n", marker.display()));
                }
                // The redirection goes FIRST on purpose: `echo http://127.0.0.1:51234>%~5` makes cmd
                // read that trailing `3` as a file handle and write the URL one character short —
                // a stand-in that lies about the thing under test.
                body.push_str(&format!(">\"%~5\" echo {}\r\n", self.url));
                body.push_str(if self.lingers {
                    "ping -n 20 127.0.0.1 >nul\r\n"
                } else {
                    "exit /b 0\r\n"
                });
            } else {
                body.push_str("#!/bin/sh\n");
                if let Some(tally) = self.tally {
                    body.push_str(&format!("echo ran >> \"{}\"\n", tally.display()));
                }
                body.push_str("echo 'fake backend speaking' >&2\n");
                if let Some(marker) = self.breaks_when {
                    body.push_str(&format!("[ -f \"{}\" ] && exit 9\n", marker.display()));
                }
                body.push_str(&format!("echo '{}' > \"$5\"\n", self.url));
                body.push_str(if self.lingers { "sleep 20\n" } else { "exit 0\n" });
            }
            std::fs::write(&path, body).expect("write the stand-in backend");
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755))
                    .expect("make the stand-in executable");
            }
            path
        }
    }

    /// A process that just sits there, so a test can ask what one look at a LIVE one reports.
    fn lingering_process() -> Child {
        Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) {
                vec!["/C", "ping -n 20 127.0.0.1 >nul"]
            } else {
                vec!["-c", "sleep 20"]
            })
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn a lingering process")
    }

    fn dead_process() -> Child {
        let mut child = Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) { vec!["/C", "exit 0"] } else { vec!["-c", "exit 0"] })
            .stderr(Stdio::piped())
            .spawn()
            .expect("spawn");
        let _ = child.wait();
        child
    }

    fn backend_of(child: Child, url: &str) -> Backend {
        Backend {
            child,
            url: url.to_string(),
            stderr: Arc::new(Mutex::new(VecDeque::new())),
        }
    }

    /// Everything the supervisor has announced once one of them starts with `needle`.
    fn wait_for(seen: &Arc<Mutex<Vec<String>>>, needle: &str, patience: Duration) -> Vec<String> {
        let start = Instant::now();
        while start.elapsed() < patience {
            let notes = seen.lock().unwrap().clone();
            if notes.iter().any(|n| n.starts_with(needle)) {
                return notes;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        panic!("the supervisor never said {needle:?}: {:?}", seen.lock().unwrap());
    }

    /// Start a supervisor over `state`, collecting what it announces.
    fn watch(
        state: &Arc<Sidecar>,
        paths: &Paths,
        tuning: Tuning,
    ) -> (Arc<Mutex<Vec<String>>>, std::thread::JoinHandle<()>) {
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let (state, paths, sink) = (Arc::clone(state), paths.clone(), Arc::clone(&seen));
        let handle = std::thread::spawn(move || {
            // English explicitly, so the assertions below read in English wherever this runs.
            supervise(state, paths, tuning, &DIALOGO[0], move |event| {
                let note = match event {
                    Supervised::Restarted(url) => format!("restarted {url}"),
                    Supervised::GaveUp(why) => format!("gave up {why}"),
                };
                sink.lock().unwrap().push(note);
            });
        });
        (seen, handle)
    }

    /// End the session the way the app's exit hook does, and wait for the thread to notice.
    fn stop(state: &Arc<Sidecar>, watcher: std::thread::JoinHandle<()>) {
        state.stopping.store(true, Ordering::SeqCst);
        if let Some(mut backend) = state.backend.lock().unwrap().take() {
            let _ = backend.child.kill();
        }
        watcher.join().expect("the supervisor thread ended");
    }

    /// The regression the whole change exists for: a backend that dies mid-session comes back
    /// without the user doing anything, and the dead one's last words survive the replacement.
    #[test]
    fn a_dead_backend_is_started_again_without_the_user_doing_anything() {
        let dir = scratch("restart");
        // The test owns the socket the stand-in "serves" on: `start_sidecar` waits for the port to
        // accept a connection, and a batch file cannot listen. What is under test is the shell's
        // reaction to the process, not the socket underneath it.
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port to pretend on");
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let paths = Paths {
            exe: Fake { dir: &dir, url: &url, lingers: true, breaks_when: None, tally: None }
                .write(),
            data_dir: dir.clone(),
            port_file: dir.join("port.txt"),
        };
        let tuning = quick(Tuning::default());

        let first = start_sidecar(&paths, 0, tuning.budget).expect("the stand-in backend came up");
        assert_eq!(first.url, url, "the announced URL is what the shell adopted");
        let first_pid = first.child.id();

        let state = Arc::new(Sidecar {
            backend: Mutex::new(Some(first)),
            stopping: AtomicBool::new(false),
        });
        let (seen, watcher) = watch(&state, &paths, tuning);

        // Kill it the way a crash would.
        state.backend.lock().unwrap().as_mut().unwrap().child.kill().expect("kill the backend");

        let notes = wait_for(&seen, "restarted", Duration::from_secs(30));
        assert_eq!(notes, vec![format!("restarted {url}")], "one restart, on the same origin");

        {
            let mut guard = state.backend.lock().unwrap();
            let fresh = guard.as_mut().expect("the supervisor put a backend back");
            assert_ne!(fresh.child.id(), first_pid, "the replacement cannot be the dead process");
            assert_eq!(look(fresh), Health::Serving);
        }

        let crash = std::fs::read_to_string(dir.join("backend-crash.txt"))
            .expect("a crash report was written before the replacement");
        assert!(
            crash.contains("fake backend speaking"),
            "the dead backend's own words did not survive the restart: {crash}"
        );

        stop(&state, watcher);
        drop(listener);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A backend that comes up and dies, over and over, is not restarted forever.
    #[test]
    fn a_flapping_backend_burns_the_fuse_and_the_app_says_so() {
        let dir = scratch("flap");
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port to pretend on");
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let tally = dir.join("runs.txt");
        let paths = Paths {
            exe: Fake {
                dir: &dir,
                url: &url,
                lingers: false,
                breaks_when: None,
                tally: Some(&tally),
            }
            .write(),
            data_dir: dir.clone(),
            port_file: dir.join("port.txt"),
        };
        let tuning = quick(Tuning { max_in_window: 3, ..Tuning::default() });

        let first = start_sidecar(&paths, 0, tuning.budget).expect("it starts — it just does not stay");
        let state = Arc::new(Sidecar {
            backend: Mutex::new(Some(first)),
            stopping: AtomicBool::new(false),
        });
        let (seen, watcher) = watch(&state, &paths, tuning);

        let notes = wait_for(&seen, "gave up", Duration::from_secs(60));
        let verdict = notes.last().unwrap();
        assert!(verdict.contains("Close Chimera and open it again"), "{verdict}");
        assert!(verdict.contains("backend-crash.txt"), "the verdict names no file: {verdict}");

        // Counted from the stand-in's own tally rather than from the supervisor's account of
        // itself: the fuse is only real if the number of PROCESSES stops growing.
        let runs = std::fs::read_to_string(&tally).unwrap().lines().count();
        assert_eq!(
            runs,
            1 + tuning.max_in_window,
            "the shell started one and the fuse allowed {} more",
            tuning.max_in_window
        );

        stop(&state, watcher);
        drop(listener);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A backend that cannot come up AT ALL is given up on, even though every attempt is slow.
    ///
    /// This is the case a sliding window alone never catches: each failure costs a whole start
    /// budget, so the attempts never coexist inside the window and the app would respawn a
    /// permanently broken backend for as long as it stayed open.
    #[test]
    fn a_backend_that_will_not_come_up_is_given_up_on() {
        let dir = scratch("broken");
        let broken = dir.join("broken.flag");
        std::fs::write(&broken, "").expect("mark the backend broken");
        let tally = dir.join("runs.txt");
        let paths = Paths {
            exe: Fake {
                dir: &dir,
                url: "http://127.0.0.1:1",
                lingers: false,
                breaks_when: Some(&broken),
                tally: Some(&tally),
            }
            .write(),
            data_dir: dir.clone(),
            port_file: dir.join("port.txt"),
        };
        let tuning = quick(Tuning {
            // Short, because here the budget is the cost: this is the one test that waits for a
            // start to time out, three times over.
            budget: Budget {
                url: Duration::from_millis(400),
                listen: Duration::from_millis(400),
            },
            max_consecutive_failures: 2,
            ..Tuning::default()
        });

        // Start from a backend that is already dead rather than from a start that has to succeed:
        // a stand-in that never comes up cannot provide the first one, and this test is about what
        // happens after the first death anyway.
        let state = Arc::new(Sidecar {
            backend: Mutex::new(Some(backend_of(dead_process(), "http://127.0.0.1:1"))),
            stopping: AtomicBool::new(false),
        });
        let (seen, watcher) = watch(&state, &paths, tuning);

        let notes = wait_for(&seen, "gave up", Duration::from_secs(60));
        assert!(
            notes.iter().all(|n| n.starts_with("gave up")),
            "nothing came back, so nothing should have been announced as restarted: {notes:?}"
        );
        assert!(
            notes[0].contains("startup-failure.txt"),
            "the verdict does not point at what the failed start left behind: {}",
            notes[0]
        );
        let report = std::fs::read_to_string(dir.join("startup-failure.txt"))
            .expect("a startup report from the last failed attempt");
        assert!(
            report.contains("fake backend speaking"),
            "a failed RESTART lost the backend's own words: {report}"
        );
        let runs = std::fs::read_to_string(&tally).unwrap().lines().count();
        assert_eq!(runs, tuning.max_consecutive_failures, "the fuse did not bound the attempts");

        stop(&state, watcher);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// The agent's file tools must not be rooted where the app keeps its own `.env`.
    ///
    /// Without `CHIMERA_WORKSPACE`, `resolve_app_workspace` falls through to the process CWD, which
    /// for an installed build is the launcher's directory — the folder holding `.env`. Measured on
    /// a real install before this: `read_file(".env")` returned `OPENROUTER_API_KEY=sk-or-v1-…` in
    /// full, and a scheduled job walking "the project" walked 4757 files and was abandoned at
    /// 1800s, five nights running.
    ///
    /// Behavioural on purpose: the stand-in writes the variable it actually received, so this fails
    /// if the line is dropped AND if it is set to something the child never sees.
    #[test]
    fn the_backend_is_told_where_to_root_its_tools() {
        let dir = scratch("workspace-env");
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port to pretend on");
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let seen = dir.join("seen.txt");
        let exe = dir.join(if cfg!(windows) { "backend.cmd" } else { "backend.sh" });
        let body = if cfg!(windows) {
            format!(">\"{}\" echo %CHIMERA_WORKSPACE%\r\n>\"%~5\" echo {url}\r\nexit /b 0\r\n", seen.display())
        } else {
            format!("#!/bin/sh\necho \"$CHIMERA_WORKSPACE\" > \"{}\"\necho '{url}' > \"$5\"\nexit 0\n", seen.display())
        };
        std::fs::write(&exe, body).expect("write the stand-in backend");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let paths =
            Paths { exe, data_dir: dir.clone(), port_file: dir.join("port.txt") };

        let mut backend =
            start_sidecar(&paths, 0, quick(Tuning::default()).budget).expect("it came up");
        let _ = backend.child.wait();

        let got = std::fs::read_to_string(&seen).expect("the stand-in recorded its environment");
        let got = got.trim();
        assert!(
            !got.is_empty() && got != "%CHIMERA_WORKSPACE%",
            "the backend was left to guess its root from the CWD: {got:?}"
        );
        assert_eq!(
            Path::new(got),
            dir.join("workspace"),
            "the root must be the app's own empty folder, never the install directory"
        );
        assert!(dir.join("workspace").is_dir(), "and it has to exist before the backend looks");
    }

    /// The Settings screen reaches the tray's switches through the backend, so the backend has to
    /// be handed the two files by name: the preferences this process reads, and the report it
    /// writes. Without them the screen's rows say "only in the desktop app" — inside the app.
    #[test]
    fn the_backend_is_told_where_the_shell_keeps_its_switches() {
        let dir = scratch("shell-prefs-env");
        let listener = TcpListener::bind("127.0.0.1:0").expect("a port to pretend on");
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let seen = dir.join("seen.txt");
        let exe = dir.join(if cfg!(windows) { "backend.cmd" } else { "backend.sh" });
        let body = if cfg!(windows) {
            format!(
                ">\"{}\" echo %CHIMERA_SHELL_PREFS%^|%CHIMERA_SHELL_STATE%\r\n>\"%~5\" echo {url}\r\nexit /b 0\r\n",
                seen.display()
            )
        } else {
            format!(
                "#!/bin/sh\necho \"$CHIMERA_SHELL_PREFS|$CHIMERA_SHELL_STATE\" > \"{}\"\necho '{url}' > \"$5\"\nexit 0\n",
                seen.display()
            )
        };
        std::fs::write(&exe, body).expect("write the stand-in backend");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let paths = Paths { exe, data_dir: dir.clone(), port_file: dir.join("port.txt") };

        let mut backend =
            start_sidecar(&paths, 0, quick(Tuning::default()).budget).expect("it came up");
        let _ = backend.child.wait();

        let got = std::fs::read_to_string(&seen).expect("the stand-in recorded its environment");
        let (prefs_file, state_file) = got.trim().split_once('|').expect("two paths");
        assert_eq!(Path::new(prefs_file.trim()), crate::prefs::prefs_path(&dir), "the preferences file");
        assert_eq!(Path::new(state_file.trim()), crate::prefs::state_path(&dir), "the report file");
    }

    #[test]
    fn a_process_that_exited_reads_as_gone() {
        let mut backend = backend_of(dead_process(), "http://127.0.0.1:1");
        assert_eq!(look(&mut backend), Health::Exited);
    }

    #[test]
    fn a_live_process_whose_port_answers_nothing_reads_as_silent() {
        // A port that was bound and released: nothing listens there now and the process is alive —
        // exactly the state a `try_wait`-only check calls healthy.
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        let mut backend = backend_of(lingering_process(), &format!("http://127.0.0.1:{port}"));
        assert_eq!(look(&mut backend), Health::Silent);
        let _ = backend.child.kill();
    }

    #[test]
    fn a_live_process_with_a_listener_reads_as_serving() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let mut backend = backend_of(lingering_process(), &url);
        assert_eq!(look(&mut backend), Health::Serving);
        let _ = backend.child.kill();
    }

    #[test]
    fn the_fuse_allows_a_burst_and_then_stops_it() {
        let t0 = Instant::now();
        let mut fuse = Fuse::new(&Tuning { max_in_window: 3, ..Tuning::default() });
        for i in 0..3 {
            assert!(fuse.allows(t0 + Duration::from_secs(i)), "restart {i} should be allowed");
        }
        assert!(!fuse.allows(t0 + Duration::from_secs(4)), "the fourth is over budget");
        // …and the budget is a WINDOW, not a lifetime cap: a crash an hour later is not the same
        // event as a crash loop, and refusing it would strand a session that could recover.
        assert!(fuse.allows(t0 + Duration::from_secs(200)));
    }

    #[test]
    #[cfg(windows)]
    fn the_backend_is_spawned_without_a_console_window() {
        // A user opened the app and got a black terminal beside it, printing the port and the cron
        // tick. It is not decoration and it is not avoidable by dropping either half: the shell has
        // no console (windows_subsystem = "windows") and the frozen sidecar is a console binary
        // (PyInstaller --console, which is what keeps its piped stderr real), so Windows allocates
        // a console for the child. CREATE_NO_WINDOW is the only thing that suppresses it.
        //
        // Read from the source, because std::process::Command exposes no way to read a creation
        // flag back — there is nothing to assert on a built Command. That makes this a guard
        // against the line being dropped, not proof that Windows honoured it; the proof is an
        // installed build with no terminal next to it.
        let src = include_str!("main.rs");
        let spawn = src
            .split("fn start_sidecar")
            .nth(1)
            .expect("start_sidecar is where the backend is launched");
        let body = &spawn[..spawn.find("
}
").unwrap_or(spawn.len())];
        assert!(
            body.contains("hide_console(&mut cmd)"),
            "start_sidecar spawns the backend without going through hide_console"
        );
        assert!(
            src.contains("const CREATE_NO_WINDOW: u32 = 0x0800_0000"),
            "hide_console no longer sets CREATE_NO_WINDOW"
        );
    }

    #[test]
    fn a_backend_that_never_starts_burns_the_fuse_even_when_each_attempt_is_slow() {
        // Three attempts 75 seconds apart never coexist in a 120-second window, so the window alone
        // would let this run forever. This is the second limit, and this is why it is there.
        let t0 = Instant::now();
        let mut fuse =
            Fuse::new(&Tuning { max_consecutive_failures: 3, ..Tuning::default() });
        for i in 0..3 {
            assert!(fuse.allows(t0 + Duration::from_secs(i * 75)), "attempt {i}");
            fuse.attempt_ended(false);
        }
        assert!(!fuse.allows(t0 + Duration::from_secs(225)));
    }

    #[test]
    fn a_start_that_worked_clears_the_run_of_failures() {
        let t0 = Instant::now();
        let mut fuse = Fuse::new(&Tuning { max_consecutive_failures: 2, ..Tuning::default() });
        assert!(fuse.allows(t0));
        fuse.attempt_ended(false);
        assert!(fuse.allows(t0 + Duration::from_secs(1)));
        fuse.attempt_ended(true); // it came up this time
        assert!(
            fuse.allows(t0 + Duration::from_secs(2)),
            "a backend that recovered once is not a backend that cannot start"
        );
    }

    /// A source-level assertion, for the same reason as `the_sidecar_actually_pipes_its_stderr`:
    /// `main`'s setup closure cannot run without a `tauri::App`.
    ///
    /// It guards the one regression the tests above cannot see — a supervisor that is written,
    /// tested, and never started. This file already carries the lesson in another form: a guard
    /// outside the flow guards nothing.
    ///
    /// `rsplit_once`, and I learned why the way the test above says you learn it. The first version
    /// searched forward from the first `fn main()` in the file — which is the string literal two
    /// lines below — so the window contained its own answer and stayed GREEN with the supervisor
    /// call deleted. The real definitions are the LAST occurrence of each name in this file.
    #[test]
    fn the_app_starts_the_supervisor_and_shuts_it_down() {
        let source = include_str!("main.rs");
        let main_body = source.rsplit_once("fn main()").expect("main exists").1;
        assert!(
            main_body.contains("supervise("),
            "the supervisor is never started — a backend that dies stays dead"
        );
        let kill = source.rsplit_once("fn kill_sidecar").expect("kill_sidecar exists").1;
        let kill = kill.split_once("\nfn ").map_or(kill, |(head, _)| head);
        assert!(
            kill.contains("stopping.store(true"),
            "quitting no longer tells the supervisor to stop — it can respawn a backend that then \
             outlives the window"
        );
    }
}

fn kill_sidecar(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<Arc<Sidecar>>() {
        // Set BEFORE the child is taken. The supervisor reads this flag under the same lock, so a
        // restart already in flight kills its own replacement instead of orphaning it — see
        // `Sidecar`. Storing it after the take would leave exactly that race open.
        state.stopping.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = state.backend.lock() {
            if let Some(mut backend) = guard.take() {
                let _ = backend.child.kill();
            }
        }
    }
}

/// One language's worth of the update dialog.
///
/// A struct rather than a tuple because it grew: the tray's manual check needs a menu label,
/// an "already up to date" line and a failure line on top of the four the automatic check
/// used, and `DIALOGO[i].4` stops being readable somewhere around the fifth field.
struct Dialogo {
    codigo: &'static str,
    /// The tray item. Its presence is the whole point of the manual path: the automatic check
    /// runs once at startup and says nothing when there is no update, so without this there is
    /// no way to ASK — you wait for the next launch and hope.
    menu: &'static str,
    titulo: &'static str,
    mensagem: &'static str,
    atualizar: &'static str,
    depois: &'static str,
    /// Only ever shown for a check the user ASKED for. Saying "you are up to date" unprompted
    /// at every launch is the nagging the automatic path exists to avoid.
    atual: &'static str,
    falhou: &'static str,
    /// The tray's Quit item.
    sair: &'static str,
    nao_iniciou_titulo: &'static str,
    /// The lead sentence of the startup failure. What follows it is the technical detail, and that
    /// stays in ENGLISH on purpose: it is a path, an OS error or the backend's own stderr, it goes
    /// into a bug report, and a translated system error is one nobody can search for.
    nao_iniciou: &'static str,
    parou_titulo: &'static str,
    /// `{n}` is how many times the app tried to bring the backend back.
    parou: &'static str,
    relatorio: &'static str,
    ultima_tentativa: &'static str,
    // --- The tray's switches (study 29, P2.2, P2.3, P2.4, P2.6). ---
    /// Brings the window back: the only way to it once closing it only hides it.
    abrir: &'static str,
    manter_na_bandeja: &'static str,
    iniciar_com_sistema: &'static str,
    chamar_atencao: &'static str,
    /// `{k}` is the chord, as the person will press it.
    atalho: &'static str,
    /// `{k}` the chord, `{e}` why it could not be registered (another program holds it, or it does
    /// not parse). Shown in the tray menu rather than swallowed.
    atalho_falhou: &'static str,
    autostart_falhou: &'static str,
    /// `{f}` the preferences file, `{e}` the OS's words.
    prefs_falhou: &'static str,
    /// `{f}` where a preferences file that did not parse was moved before the first save over it,
    /// so a hand edit with a typo is kept rather than overwritten by the click of a switch.
    prefs_guardado: &'static str,
    /// `{e}` is what the backend answered. The tray must not read a refusal as "nothing pending".
    sem_acesso: &'static str,
    /// Tooltip pieces, shown only while the app keeps running in the tray.
    dica_rodando: &'static str,
    dica_gasto: &'static str,
    dica_aguardando: &'static str,
}

/// The update dialog, in the ten languages the app ships.
///
/// `{v}` is the version and `{e}` the error, substituted at runtime — `format!` cannot be used
/// because the template is a lookup, not a literal, and a translation that dropped a
/// placeholder would be a silent hole rather than a compile error.
/// `dialogo_mantem_os_marcadores` is the test that closes that.
///
/// Through 0.48.1 this was English for everyone: the one screen in a ten-language app that did
/// not speak the user's language, and the one that asks permission to change their machine.
const DIALOGO: [Dialogo; 10] = [
    Dialogo {
        codigo: "en",
        menu: "Check for updates",
        titulo: "Chimera update available",
        mensagem: "A new version (v{v}) is available. Download and install now? The app will restart.",
        atualizar: "Update & Restart",
        depois: "Later",
        atual: "Chimera is up to date (v{v}).",
        falhou: "Could not check for updates: {e}",
        sair: "Quit Chimera",
        nao_iniciou_titulo: "Chimera could not start",
        nao_iniciou: "Chimera could not start its backend. The detail below is for a bug report:",
        parou_titulo: "Chimera's backend stopped",
        parou: "Chimera's backend stopped, and the app could not bring it back (it tried {n} times).\n\nClose Chimera and open it again.",
        relatorio: "What the backend said before it stopped:",
        ultima_tentativa: "The last attempt to restart it:",
        abrir: "Open Chimera",
        manter_na_bandeja: "Keep running in the tray when closed",
        iniciar_com_sistema: "Start at sign-in, in the tray",
        chamar_atencao: "Flash the taskbar when an approval is waiting",
        atalho: "Quick-entry shortcut ({k})",
        atalho_falhou: "Shortcut {k} is not active: {e}",
        autostart_falhou: "Could not change start at sign-in: {e}",
        prefs_falhou: "Tray preferences not saved or read ({f}): {e}",
        prefs_guardado: "The tray preferences file did not parse; it was kept as {f} and replaced by the switches as they are now",
        sem_acesso: "No access to the backend ({e}): approvals and spending cannot be shown here",
        dica_rodando: "{n} running",
        dica_gasto: "US$ {usd} today (UTC)",
        dica_aguardando: "{n} waiting for you",
    },
    Dialogo {
        codigo: "pt",
        menu: "Verificar atualizações",
        titulo: "Atualização do Chimera disponível",
        mensagem: "Uma versão nova (v{v}) está disponível. Baixar e instalar agora? O app vai reiniciar.",
        atualizar: "Atualizar e reiniciar",
        depois: "Depois",
        atual: "O Chimera está atualizado (v{v}).",
        falhou: "Não foi possível verificar atualizações: {e}",
        sair: "Sair do Chimera",
        nao_iniciou_titulo: "O Chimera não conseguiu iniciar",
        nao_iniciou: "O Chimera não conseguiu iniciar o backend. O detalhe abaixo é para um relatório de bug:",
        parou_titulo: "O backend do Chimera parou",
        parou: "O backend do Chimera parou e o app não conseguiu trazê-lo de volta (tentou {n} vezes).\n\nFeche o Chimera e abra de novo.",
        relatorio: "O que o backend disse antes de parar:",
        ultima_tentativa: "A última tentativa de reiniciá-lo:",
        abrir: "Abrir o Chimera",
        manter_na_bandeja: "Manter rodando na bandeja ao fechar",
        iniciar_com_sistema: "Iniciar com o sistema, na bandeja",
        chamar_atencao: "Piscar a barra de tarefas quando houver aprovação pendente",
        atalho: "Atalho de entrada rápida ({k})",
        atalho_falhou: "O atalho {k} não está ativo: {e}",
        autostart_falhou: "Não foi possível mudar o início com o sistema: {e}",
        prefs_falhou: "Preferências da bandeja não gravadas ou lidas ({f}): {e}",
        prefs_guardado: "O arquivo de preferências da bandeja não pôde ser lido; foi guardado como {f} e substituído pelas opções atuais",
        sem_acesso: "Sem acesso ao backend ({e}): aprovações e gasto não podem ser mostrados aqui",
        dica_rodando: "{n} rodando",
        dica_gasto: "US$ {usd} hoje (UTC)",
        dica_aguardando: "{n} esperando por você",
    },
    Dialogo {
        codigo: "es",
        menu: "Buscar actualizaciones",
        titulo: "Actualización de Chimera disponible",
        mensagem: "Hay una versión nueva (v{v}). ¿Descargar e instalar ahora? La app se reiniciará.",
        atualizar: "Actualizar y reiniciar",
        depois: "Más tarde",
        atual: "Chimera está actualizado (v{v}).",
        falhou: "No se pudo buscar actualizaciones: {e}",
        sair: "Salir de Chimera",
        nao_iniciou_titulo: "Chimera no pudo iniciar",
        nao_iniciou: "Chimera no pudo iniciar su backend. El detalle de abajo es para un informe de error:",
        parou_titulo: "El backend de Chimera se detuvo",
        parou: "El backend de Chimera se detuvo y la app no pudo recuperarlo (lo intentó {n} veces).\n\nCierra Chimera y ábrelo de nuevo.",
        relatorio: "Lo que dijo el backend antes de detenerse:",
        ultima_tentativa: "El último intento de reiniciarlo:",
        abrir: "Abrir Chimera",
        manter_na_bandeja: "Seguir en la bandeja al cerrar",
        iniciar_com_sistema: "Iniciar con el sistema, en la bandeja",
        chamar_atencao: "Hacer parpadear la barra de tareas cuando haya una aprobación pendiente",
        atalho: "Atajo de entrada rápida ({k})",
        atalho_falhou: "El atajo {k} no está activo: {e}",
        autostart_falhou: "No se pudo cambiar el inicio con el sistema: {e}",
        prefs_falhou: "Preferencias de la bandeja no guardadas o leídas ({f}): {e}",
        prefs_guardado: "El archivo de preferencias de la bandeja no se pudo leer; se guardó como {f} y se reemplazó por las opciones actuales",
        sem_acesso: "Sin acceso al backend ({e}): aquí no se pueden mostrar aprobaciones ni gasto",
        dica_rodando: "{n} en curso",
        dica_gasto: "US$ {usd} hoy (UTC)",
        dica_aguardando: "{n} esperándote",
    },
    Dialogo {
        codigo: "fr",
        menu: "Rechercher des mises à jour",
        titulo: "Mise à jour de Chimera disponible",
        mensagem: "Une nouvelle version (v{v}) est disponible. Télécharger et installer maintenant ? L'application redémarrera.",
        atualizar: "Mettre à jour et redémarrer",
        depois: "Plus tard",
        atual: "Chimera est à jour (v{v}).",
        falhou: "Impossible de vérifier les mises à jour : {e}",
        sair: "Quitter Chimera",
        nao_iniciou_titulo: "Chimera n'a pas pu démarrer",
        nao_iniciou: "Chimera n'a pas pu démarrer son backend. Le détail ci-dessous sert à un rapport de bogue :",
        parou_titulo: "Le backend de Chimera s'est arrêté",
        parou: "Le backend de Chimera s'est arrêté et l'application n'a pas pu le relancer ({n} tentatives).\n\nFermez Chimera et rouvrez-le.",
        relatorio: "Ce que le backend a dit avant de s'arrêter :",
        ultima_tentativa: "La dernière tentative de redémarrage :",
        abrir: "Ouvrir Chimera",
        manter_na_bandeja: "Rester dans la zone de notification à la fermeture",
        iniciar_com_sistema: "Démarrer avec le système, dans la zone de notification",
        chamar_atencao: "Faire clignoter la barre des tâches quand une approbation attend",
        atalho: "Raccourci de saisie rapide ({k})",
        atalho_falhou: "Le raccourci {k} n'est pas actif : {e}",
        autostart_falhou: "Impossible de modifier le démarrage avec le système : {e}",
        prefs_falhou: "Préférences de la zone de notification non enregistrées ou lues ({f}) : {e}",
        prefs_guardado: "Le fichier de préférences de la zone de notification était illisible ; il a été conservé sous {f} et remplacé par les options actuelles",
        sem_acesso: "Pas d'accès au backend ({e}) : les approbations et les dépenses ne peuvent pas être affichées ici",
        dica_rodando: "{n} en cours",
        dica_gasto: "{usd} US$ aujourd'hui (UTC)",
        dica_aguardando: "{n} en attente de vous",
    },
    Dialogo {
        codigo: "de",
        menu: "Nach Updates suchen",
        titulo: "Chimera-Update verfügbar",
        mensagem: "Eine neue Version (v{v}) ist verfügbar. Jetzt herunterladen und installieren? Die App startet neu.",
        atualizar: "Aktualisieren und neu starten",
        depois: "Später",
        atual: "Chimera ist aktuell (v{v}).",
        falhou: "Nach Updates konnte nicht gesucht werden: {e}",
        sair: "Chimera beenden",
        nao_iniciou_titulo: "Chimera konnte nicht starten",
        nao_iniciou: "Chimera konnte sein Backend nicht starten. Die Details unten sind für einen Fehlerbericht:",
        parou_titulo: "Chimeras Backend wurde beendet",
        parou: "Chimeras Backend wurde beendet und die App konnte es nicht wiederherstellen ({n} Versuche).\n\nSchließen Sie Chimera und öffnen Sie es erneut.",
        relatorio: "Was das Backend zuletzt gemeldet hat:",
        ultima_tentativa: "Der letzte Neustartversuch:",
        abrir: "Chimera öffnen",
        manter_na_bandeja: "Beim Schließen im Infobereich weiterlaufen",
        iniciar_com_sistema: "Mit dem System starten, im Infobereich",
        chamar_atencao: "Taskleiste blinken lassen, wenn eine Freigabe wartet",
        atalho: "Schnelleingabe-Tastenkürzel ({k})",
        atalho_falhou: "Das Tastenkürzel {k} ist nicht aktiv: {e}",
        autostart_falhou: "Autostart konnte nicht geändert werden: {e}",
        prefs_falhou: "Einstellungen des Infobereichs nicht gespeichert oder gelesen ({f}): {e}",
        prefs_guardado: "Die Einstellungsdatei des Infobereichs war nicht lesbar; sie wurde als {f} aufbewahrt und durch die aktuellen Schalter ersetzt",
        sem_acesso: "Kein Zugriff auf das Backend ({e}): Freigaben und Kosten können hier nicht angezeigt werden",
        dica_rodando: "{n} laufen",
        dica_gasto: "US$ {usd} heute (UTC)",
        dica_aguardando: "{n} warten auf Sie",
    },
    Dialogo {
        codigo: "it",
        menu: "Controlla aggiornamenti",
        titulo: "Aggiornamento di Chimera disponibile",
        mensagem: "È disponibile una nuova versione (v{v}). Scaricare e installare ora? L'app si riavvierà.",
        atualizar: "Aggiorna e riavvia",
        depois: "Più tardi",
        atual: "Chimera è aggiornato (v{v}).",
        falhou: "Impossibile controllare gli aggiornamenti: {e}",
        sair: "Esci da Chimera",
        nao_iniciou_titulo: "Chimera non è riuscito ad avviarsi",
        nao_iniciou: "Chimera non è riuscito ad avviare il backend. Il dettaglio qui sotto serve per una segnalazione di bug:",
        parou_titulo: "Il backend di Chimera si è fermato",
        parou: "Il backend di Chimera si è fermato e l'app non è riuscita a riavviarlo ({n} tentativi).\n\nChiudi Chimera e riaprilo.",
        relatorio: "Che cosa ha detto il backend prima di fermarsi:",
        ultima_tentativa: "L'ultimo tentativo di riavviarlo:",
        abrir: "Apri Chimera",
        manter_na_bandeja: "Resta nell'area di notifica alla chiusura",
        iniciar_com_sistema: "Avvia con il sistema, nell'area di notifica",
        chamar_atencao: "Fai lampeggiare la barra delle applicazioni quando un'approvazione è in attesa",
        atalho: "Scorciatoia di inserimento rapido ({k})",
        atalho_falhou: "La scorciatoia {k} non è attiva: {e}",
        autostart_falhou: "Impossibile modificare l'avvio con il sistema: {e}",
        prefs_falhou: "Preferenze dell'area di notifica non salvate o lette ({f}): {e}",
        prefs_guardado: "Il file delle preferenze dell'area di notifica non era leggibile; è stato conservato come {f} e sostituito dalle opzioni attuali",
        sem_acesso: "Nessun accesso al backend ({e}): approvazioni e spesa non possono essere mostrate qui",
        dica_rodando: "{n} in esecuzione",
        dica_gasto: "US$ {usd} oggi (UTC)",
        dica_aguardando: "{n} in attesa di te",
    },
    Dialogo {
        codigo: "pl",
        menu: "Sprawdź aktualizacje",
        titulo: "Dostępna aktualizacja Chimery",
        mensagem: "Dostępna jest nowa wersja (v{v}). Pobrać i zainstalować teraz? Aplikacja uruchomi się ponownie.",
        atualizar: "Zaktualizuj i uruchom ponownie",
        depois: "Później",
        atual: "Chimera jest aktualna (v{v}).",
        falhou: "Nie udało się sprawdzić aktualizacji: {e}",
        sair: "Zakończ Chimerę",
        nao_iniciou_titulo: "Chimera nie mogła się uruchomić",
        nao_iniciou: "Chimera nie mogła uruchomić swojego backendu. Szczegóły poniżej są do zgłoszenia błędu:",
        parou_titulo: "Backend Chimery zatrzymał się",
        parou: "Backend Chimery zatrzymał się, a aplikacja nie zdołała go przywrócić (prób: {n}).\n\nZamknij Chimerę i otwórz ją ponownie.",
        relatorio: "Co backend powiedział, zanim się zatrzymał:",
        ultima_tentativa: "Ostatnia próba ponownego uruchomienia:",
        abrir: "Otwórz Chimerę",
        manter_na_bandeja: "Działaj w zasobniku po zamknięciu",
        iniciar_com_sistema: "Uruchamiaj z systemem, w zasobniku",
        chamar_atencao: "Migaj paskiem zadań, gdy czeka zatwierdzenie",
        atalho: "Skrót szybkiego wprowadzania ({k})",
        atalho_falhou: "Skrót {k} nie jest aktywny: {e}",
        autostart_falhou: "Nie udało się zmienić uruchamiania z systemem: {e}",
        prefs_falhou: "Ustawienia zasobnika nie zostały zapisane ani odczytane ({f}): {e}",
        prefs_guardado: "Plik ustawień zasobnika był nieczytelny; zachowano go jako {f} i zastąpiono bieżącymi ustawieniami",
        sem_acesso: "Brak dostępu do backendu ({e}): nie można tu pokazać zatwierdzeń ani wydatków",
        dica_rodando: "Uruchomione: {n}",
        dica_gasto: "US$ {usd} dzisiaj (UTC)",
        dica_aguardando: "Czeka na ciebie: {n}",
    },
    Dialogo {
        codigo: "zh",
        menu: "检查更新",
        titulo: "Chimera 有可用更新",
        mensagem: "有新版本 (v{v})。现在下载并安装吗？应用将重启。",
        atualizar: "更新并重启",
        depois: "稍后",
        atual: "Chimera 已是最新版本 (v{v})。",
        falhou: "无法检查更新：{e}",
        sair: "退出 Chimera",
        nao_iniciou_titulo: "Chimera 无法启动",
        nao_iniciou: "Chimera 无法启动后端。下面的细节用于提交问题报告：",
        parou_titulo: "Chimera 的后端已停止",
        parou: "Chimera 的后端已停止，应用无法将其恢复（已尝试 {n} 次）。\n\n请关闭 Chimera 后重新打开。",
        relatorio: "后端停止前的输出：",
        ultima_tentativa: "最后一次重启尝试：",
        abrir: "打开 Chimera",
        manter_na_bandeja: "关闭时保留在托盘中运行",
        iniciar_com_sistema: "随系统启动（在托盘中）",
        chamar_atencao: "有待批准事项时闪烁任务栏",
        atalho: "快速输入快捷键（{k}）",
        atalho_falhou: "快捷键 {k} 未生效：{e}",
        autostart_falhou: "无法更改随系统启动：{e}",
        prefs_falhou: "托盘偏好未能保存或读取（{f}）：{e}",
        prefs_guardado: "托盘偏好文件无法解析；已保留为 {f}，并以当前开关状态替换",
        sem_acesso: "无法访问后端（{e}）：此处无法显示待批准事项和花费",
        dica_rodando: "{n} 个运行中",
        dica_gasto: "今日 US$ {usd}（UTC）",
        dica_aguardando: "{n} 个等待你处理",
    },
    Dialogo {
        codigo: "ja",
        menu: "アップデートを確認",
        titulo: "Chimera のアップデートがあります",
        mensagem: "新しいバージョン (v{v}) があります。今すぐダウンロードしてインストールしますか？アプリは再起動します。",
        atualizar: "更新して再起動",
        depois: "後で",
        atual: "Chimera は最新です (v{v})。",
        falhou: "アップデートを確認できませんでした: {e}",
        sair: "Chimera を終了",
        nao_iniciou_titulo: "Chimera を起動できませんでした",
        nao_iniciou: "Chimera はバックエンドを起動できませんでした。以下の詳細は不具合報告用です:",
        parou_titulo: "Chimera のバックエンドが停止しました",
        parou: "Chimera のバックエンドが停止し、アプリは復帰させられませんでした ({n} 回試行)。\n\nChimera を閉じて開き直してください。",
        relatorio: "停止する前にバックエンドが出力した内容:",
        ultima_tentativa: "最後の再起動の試み:",
        abrir: "Chimera を開く",
        manter_na_bandeja: "閉じてもトレイで実行を続ける",
        iniciar_com_sistema: "システムと一緒に起動（トレイに常駐）",
        chamar_atencao: "承認待ちがあるときにタスクバーを点滅させる",
        atalho: "クイック入力のショートカット（{k}）",
        atalho_falhou: "ショートカット {k} は有効ではありません: {e}",
        autostart_falhou: "システムと一緒に起動する設定を変更できませんでした: {e}",
        prefs_falhou: "トレイの設定を保存または読み込めませんでした ({f}): {e}",
        prefs_guardado: "トレイの設定ファイルを解析できませんでした。{f} として保存し、現在の設定で置き換えました",
        sem_acesso: "バックエンドにアクセスできません ({e}): 承認待ちと支出はここに表示できません",
        dica_rodando: "{n} 件実行中",
        dica_gasto: "本日 US$ {usd}（UTC）",
        dica_aguardando: "{n} 件があなたを待っています",
    },
    Dialogo {
        codigo: "ru",
        menu: "Проверить обновления",
        titulo: "Доступно обновление Chimera",
        mensagem: "Доступна новая версия (v{v}). Скачать и установить сейчас? Приложение перезапустится.",
        atualizar: "Обновить и перезапустить",
        depois: "Позже",
        atual: "Chimera обновлена до последней версии (v{v}).",
        falhou: "Не удалось проверить обновления: {e}",
        sair: "Выйти из Chimera",
        nao_iniciou_titulo: "Chimera не смогла запуститься",
        nao_iniciou: "Chimera не смогла запустить бэкенд. Подробности ниже — для отчёта об ошибке:",
        parou_titulo: "Бэкенд Chimera остановился",
        parou: "Бэкенд Chimera остановился, и приложение не смогло его восстановить (попыток: {n}).\n\nЗакройте Chimera и откройте снова.",
        relatorio: "Что бэкенд сообщил перед остановкой:",
        ultima_tentativa: "Последняя попытка перезапуска:",
        abrir: "Открыть Chimera",
        manter_na_bandeja: "При закрытии оставаться в трее",
        iniciar_com_sistema: "Запускать вместе с системой, в трее",
        chamar_atencao: "Мигать панелью задач, когда ждёт одобрение",
        atalho: "Сочетание клавиш быстрого ввода ({k})",
        atalho_falhou: "Сочетание {k} не активно: {e}",
        autostart_falhou: "Не удалось изменить автозапуск: {e}",
        prefs_falhou: "Настройки трея не сохранены или не прочитаны ({f}): {e}",
        prefs_guardado: "Файл настроек трея не удалось разобрать; он сохранён как {f} и заменён текущими настройками",
        sem_acesso: "Нет доступа к бэкенду ({e}): одобрения и расходы здесь показать нельзя",
        dica_rodando: "Выполняется: {n}",
        dica_gasto: "US$ {usd} сегодня (UTC)",
        dica_aguardando: "Ждут вас: {n}",
    },
];

/// Pick the dialog's language from an OS locale tag.
///
/// **This reads the OPERATING SYSTEM's language, not the one picked inside the app**, and the
/// distinction is worth writing down because it is a real limitation with a real reason.
///
/// The app stores its language in `localStorage` (`chimera.lang` in `src/lib/i18n.tsx`), which
/// this process cannot read: the window loads the sidecar's `http://127.0.0.1:PORT` origin, and
/// `capabilities/default.json` deliberately grants no IPC to it. Getting the value here would mean
/// opening a Tauri command to an http origin — widening the security surface of the app to
/// translate one dialog, which is the wrong trade.
///
/// It is right for everybody who never changed it, because `detectLang()` falls back to
/// `navigator.language`, which is this same locale. It is wrong only for someone who deliberately
/// picked a different language in the app — and then it shows their system language rather than
/// English, which is still closer than what shipped before.
fn idioma_do_dialogo(locale: Option<&str>) -> &'static Dialogo {
    // "pt-BR" -> "pt", "zh-Hans-CN" -> "zh". Lowercased: macOS reports "pt_BR", Windows "pt-BR".
    let base = locale
        .unwrap_or("en")
        .split(['-', '_'])
        .next()
        .unwrap_or("en")
        .to_ascii_lowercase();
    DIALOGO
        .iter()
        .find(|d| d.codigo == base)
        .unwrap_or(&DIALOGO[0])
}

/// The dialog for the machine's language. One place, so the tray label and the dialogs it opens
/// can never disagree about which language this session is in.
fn dialogo() -> &'static Dialogo {
    idioma_do_dialogo(sys_locale::get_locale().as_deref())
}

/// How often a running app looks again.
///
/// The check used to happen once, at launch, and never again. An app that stays open — which this
/// one is, being a working tool — therefore never learned about a release: measured on 2026-09-02,
/// a user sat on 0.49.0 with 0.49.1 published and had to fetch the installer from the website by
/// hand. The tray's "check for updates" existed and is, on a default Windows install, behind the
/// hidden-icons chevron.
///
/// The `--latest=false` fix made this worse before it made it better: a release is now not offered
/// until its installers finish building, which is correct — offering one whose installers do not
/// exist is worse — but it widens the period in which the single launch-time check finds nothing,
/// from the instant of publication to the whole twenty-five minute build.
const INTERVALO_DE_CHECAGEM: Duration = Duration::from_secs(6 * 60 * 60);

/// The version the user said "Later" to, for as long as this process lives.
///
/// Deliberately in memory and not on disk: "not now" is about this sitting, not forever. The
/// frontend badge has its own persistent dismissal (`chimera.updateDismissed`) and that is a
/// different promise — this one comes back when you reopen the app.
static ADIADA: Mutex<Option<String>> = Mutex::new(None);

/// Whether to put the dialog up for this version.
///
/// The whole rule of the periodic check, extracted so it can be tested with assertions instead of
/// by reading. Without it, checking every six hours would ask every six hours — which is the
/// nagging the automatic path has always been careful to avoid, arriving through the front door.
///
/// A check the user ASKED for always asks: they clicked the menu item, and answering "you already
/// declined this one" would be refusing to do the thing they just requested.
fn deve_perguntar(pedida: bool, versao: &str, adiada: Option<&str>) -> bool {
    pedida || adiada != Some(versao)
}

/// Check GitHub for a signed update and, if the user consents, install it and relaunch.
///
/// Honest-by-construction: the signature is verified against the embedded pubkey by the updater
/// plugin (we never disable it); nothing installs without the explicit confirm dialog; and every
/// failure path (offline, no release, rate-limit, bad signature) returns `Err` so the caller can
/// stay silent — the user is never nagged and the app never crashes over an update check.
async fn check_for_update(
    app: tauri::AppHandle,
    pedida: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let d = dialogo();

    // `check()` fetches the manifest and verifies the signature; `None` = already up to date.
    let Some(update) = app.updater()?.check().await? else {
        // Silence is right at startup and WRONG after a click. A menu item that does nothing
        // visible reads as broken, and the user's next move is to click it again.
        if pedida {
            app.dialog()
                .message(d.atual.replace("{v}", app.package_info().version.to_string().as_str()))
                .title(d.titulo)
                .buttons(MessageDialogButtons::Ok)
                .blocking_show();
        }
        return Ok(());
    };

    // Asked already, and declined? Then this automatic round says nothing. A newer version has a
    // different string and asks again, which falls out of comparing rather than needing a rule.
    let adiada = ADIADA.lock().ok().and_then(|g| g.clone());
    if !deve_perguntar(pedida, &update.version, adiada.as_deref()) {
        return Ok(());
    }

    let confirmed = app
        .dialog()
        .message(d.mensagem.replace("{v}", &update.version))
        .title(d.titulo)
        .buttons(MessageDialogButtons::OkCancelCustom(
            d.atualizar.to_string(),
            d.depois.to_string(),
        ))
        // Safe here: this runs inside `tauri::async_runtime::spawn` (a worker thread), not the main
        // thread, so blocking for the answer doesn't freeze the UI.
        .blocking_show();

    if !confirmed {
        // Remembered, so the next round in six hours does not ask about the same release again.
        if let Ok(mut adiada) = ADIADA.lock() {
            *adiada = Some(update.version.clone());
        }
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

/// Bring the main window forward: what a second launch of the app does instead of starting a
/// second backend on the same data folder.
fn bring_forward(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

// ---------------------------------------------------------------------------------------------
// The tray's switches (study 29: P2.2 keep running in the tray, P2.3 start at sign-in, P2.4 call
// attention, P2.6 the quick-entry shortcut). All of it is driven from here: the window grants no
// IPC and still does not, so the page cannot flip any of these and none of the plugins' own
// webview commands are reachable from it.
// ---------------------------------------------------------------------------------------------

/// The argument the sign-in entry launches the app with. One constant, so the plugin that writes
/// the entry and the code that reads the launch can never disagree about the spelling.
const AUTOSTART_ARG: &str = "--autostart";

/// Was this launch the sign-in entry rather than a person clicking the icon?
fn launched_by_autostart(args: impl IntoIterator<Item = String>) -> bool {
    args.into_iter().skip(1).any(|a| a == AUTOSTART_ARG)
}

/// Stand where a shortcut launch stands: in the install directory.
///
/// Not tidiness. The backend reads its `.env` — API keys, the server token — relative to its
/// working directory, which it inherits from this process, and Settings writes it there too. The
/// Start-menu and desktop shortcuts start the app in the install directory (their "Start in" is
/// it; checked on an installed 0.64), so that is where every key the owner saved lives. The
/// Windows `Run` entry gives the process whatever directory Explorer has — not that one — and
/// the app would come up at sign-in without its keys, looking configured and answering nothing.
///
/// Windows only, and only for a sign-in launch: on macOS a Finder launch already starts in `/`, the
/// same place a LaunchAgent does, so changing it there would make the sign-in launch the odd one.
fn root_at_install_dir() {
    #[cfg(windows)]
    if let Some(dir) = std::env::current_exe().ok().and_then(|exe| exe.parent().map(Path::to_path_buf)) {
        let _ = std::env::set_current_dir(dir);
    }
}

/// Why the tray has to say something, by source, so fixing one does not erase the report of another.
#[derive(Default)]
struct Problems {
    prefs: Option<String>,
    autostart: Option<String>,
    shortcut: Option<String>,
    backend: Option<String>,
}

impl Problems {
    /// One line for the tray, or nothing when all is well.
    fn line(&self) -> Option<String> {
        let all: Vec<&str> = [&self.prefs, &self.autostart, &self.shortcut, &self.backend]
            .into_iter()
            .filter_map(|p| p.as_deref())
            .collect();
        (!all.is_empty()).then(|| all.join(" · "))
    }
}

/// What the shell knows about itself that the tray and the window events share.
struct Shell {
    data_dir: PathBuf,
    prefs: Mutex<Prefs>,
    /// Whether the main window is in front. Written by its `Focused` events, read by the watcher:
    /// a flash for an approval the owner is already looking at is noise.
    focused: AtomicBool,
    problems: Mutex<Problems>,
    /// The tray line for a broken preferences file this session moved aside, kept for the rest of
    /// the session: the save after it succeeds, and clearing the line then would hide where the
    /// person's edit went one click after saying it.
    prefs_set_aside: Mutex<Option<String>>,
    /// The preferences file's modification time when it was last taken in, so the watcher parses
    /// it only when something — the Settings screen, a hand edit — changed it.
    prefs_seen: Mutex<Option<std::time::SystemTime>>,
}

impl Shell {
    fn prefs(&self) -> Prefs {
        self.prefs.lock().map(|p| p.clone()).unwrap_or_default()
    }
}

/// The tray's menu and the items that change after it is built.
///
/// `status` is a disabled line that is in the menu only while there is something wrong to say: a
/// shortcut another program holds, a sign-in entry the OS refused, a preferences file that did not
/// save, a backend that answers 401. Each of those used to be the kind of failure that is simply
/// never seen.
struct Tray {
    menu: Menu<tauri::Wry>,
    status: MenuItem<tauri::Wry>,
    status_shown: AtomicBool,
    autostart: CheckMenuItem<tauri::Wry>,
    quick_entry: CheckMenuItem<tauri::Wry>,
    /// Set again when the Settings screen changes the file: a tick left on a switch the screen
    /// turned off would be the tray contradicting the screen.
    keep_in_tray: CheckMenuItem<tauri::Wry>,
    attention: CheckMenuItem<tauri::Wry>,
}

/// Put the current problems in the menu, or take the line out when there are none — and tell the
/// Settings screen the same thing, with the operating system's answer for sign-in beside it.
fn show_problems(app: &tauri::AppHandle) {
    let (Some(shell), Some(tray)) = (app.try_state::<Arc<Shell>>(), app.try_state::<Tray>()) else {
        return;
    };
    let line = shell.problems.lock().ok().and_then(|p| p.line());
    // Every change of the line passes here, and so does every sign-in change (`apply_autostart`
    // reports through `set_problem`), so this is the one place the report can be kept current. A
    // report that does not write is not said in the tray: the tray is the place it would be said
    // in, and the screen's own row falls back to "unknown" without it.
    let _ = prefs::save_state(&shell.data_dir, app.autolaunch().is_enabled().ok(), line.as_deref());
    match line {
        Some(text) => {
            let _ = tray.status.set_text(&text);
            if !tray.status_shown.swap(true, Ordering::SeqCst) {
                let _ = tray.menu.insert(&tray.status, 0);
            }
        }
        None => {
            if tray.status_shown.swap(false, Ordering::SeqCst) {
                let _ = tray.menu.remove(&tray.status);
            }
        }
    }
}

fn set_problem(app: &tauri::AppHandle, pick: impl FnOnce(&mut Problems) -> &mut Option<String>, value: Option<String>) {
    if let Some(shell) = app.try_state::<Arc<Shell>>() {
        if let Ok(mut problems) = shell.problems.lock() {
            *pick(&mut problems) = value;
        }
    }
    show_problems(app);
}

/// Change one preference and write the file. A write that fails is SAID in the tray: the switch
/// still takes effect for this session, and the person learns it will not survive a restart. A
/// broken file that the write had to move aside is said too, with where it went.
fn change_prefs(app: &tauri::AppHandle, edit: impl FnOnce(&mut Prefs)) {
    // What the Settings screen wrote since the last tick first: this save writes the whole file,
    // and from a copy that predates the screen's change it would put the old switch back.
    sync_prefs_from_file(app);
    let Some(shell) = app.try_state::<Arc<Shell>>() else { return };
    let saved = match shell.prefs.lock() {
        Ok(mut prefs) => {
            edit(&mut prefs);
            prefs::save(&shell.data_dir, &prefs)
        }
        Err(_) => Err("preferences lock poisoned".to_string()),
    };
    let problem = match saved {
        Err(e) => Some(prefs_problem(dialogo(), &shell.data_dir, &e)),
        Ok(Some(aside)) => {
            let line = dialogo().prefs_guardado.replace("{f}", &aside.display().to_string());
            eprintln!("chimera tray: preferences file did not parse; moved to {}", aside.display());
            if let Ok(mut kept) = shell.prefs_set_aside.lock() {
                *kept = Some(line.clone());
            }
            Some(line)
        }
        Ok(None) => shell.prefs_set_aside.lock().ok().and_then(|kept| kept.clone()),
    };
    set_problem(app, |p| &mut p.prefs, problem);
}

/// Take in what the preferences file says when it changed since the last look, and act on it.
///
/// The file has a second writer now: the Settings screen, through the backend. Without this the
/// shell would read the file once at startup and the screen's switches would wait for a relaunch
/// — or worse, be overwritten by the next tray click, which saves the whole file from memory.
/// Called on the main thread (the watcher posts it there; the tray's own events already run there),
/// because it re-registers the global chord and sets menu items.
///
/// A file that does not parse is reported in the tray and changes nothing in force; it is set aside
/// only by the next save, as before, so a hand edit is never thrown away by a look.
fn sync_prefs_from_file(app: &tauri::AppHandle) {
    let Some(shell) = app.try_state::<Arc<Shell>>() else { return };
    let stamp = prefs::modified(&shell.data_dir);
    match shell.prefs_seen.lock() {
        Ok(mut seen) if *seen != stamp => *seen = stamp,
        _ => return,
    }
    let (disk, problem) = prefs::load(&shell.data_dir);
    if let Some(why) = problem {
        set_problem(app, |p| &mut p.prefs, Some(prefs_problem(dialogo(), &shell.data_dir, &why)));
        return;
    }
    // It reads now, so a "does not parse" said about an earlier version of it is no longer true.
    // The set-aside line stays, for the reason `change_prefs` keeps it.
    let kept = shell.prefs_set_aside.lock().ok().and_then(|kept| kept.clone());
    set_problem(app, |p| &mut p.prefs, kept);
    let adopted = match shell.prefs.lock() {
        Ok(mut prefs) => prefs::adopt(&mut prefs, disk),
        Err(_) => return,
    };
    if adopted.changed {
        let now = shell.prefs();
        if let Some(tray) = app.try_state::<Tray>() {
            let _ = tray.keep_in_tray.set_checked(now.keep_in_tray);
            let _ = tray.attention.set_checked(now.call_attention);
        }
    }
    if adopted.quick_changed {
        apply_quick_entry(app);
    }
    if let Some(wanted) = adopted.sign_in {
        apply_autostart(app, wanted);
        // The request is carried out; the file loses it, so it is not carried out again. What the
        // OS answered is in the report `apply_autostart` just wrote, not in this file.
        let _ = prefs::save(&shell.data_dir, &shell.prefs());
        if let Ok(mut seen) = shell.prefs_seen.lock() {
            *seen = prefs::modified(&shell.data_dir);
        }
    }
}

fn prefs_problem(d: &Dialogo, data_dir: &Path, why: &str) -> String {
    d.prefs_falhou
        .replace("{f}", &prefs::prefs_path(data_dir).display().to_string())
        .replace("{e}", why)
}

/// Turn the sign-in entry on or off, and show what the OS actually has afterwards.
///
/// The checkbox shows the OS's answer, never the click: muda ticks a check item the moment it is
/// clicked, so a refused write (a policy, a locked key) would otherwise leave a tick on a feature
/// that is off.
fn apply_autostart(app: &tauri::AppHandle, wanted: bool) {
    let manager = app.autolaunch();
    let result = if wanted { manager.enable() } else { manager.disable() };
    let problem = result
        .err()
        .map(|e| dialogo().autostart_falhou.replace("{e}", &e.to_string()));
    let actual = manager.is_enabled().unwrap_or(false);
    if let Some(tray) = app.try_state::<Tray>() {
        let _ = tray.autostart.set_checked(actual);
    }
    set_problem(app, |p| &mut p.autostart, problem);
}

/// The chord as a person reads it: `CommandOrControl` is Ctrl here and Cmd on a Mac.
fn chord_label(chord: &str) -> String {
    let local = if cfg!(target_os = "macos") { "Cmd" } else { "Ctrl" };
    chord
        .split('+')
        .map(|part| match part.trim().to_ascii_lowercase().as_str() {
            "commandorcontrol" | "commandorctrl" | "cmdorctrl" | "cmdorcontrol" => local.to_string(),
            _ => part.trim().to_string(),
        })
        .collect::<Vec<_>>()
        .join("+")
}

/// Parse the chord from the preferences file. Its own function so the failure path — a chord the
/// parser refuses — is tested without an app, and so its error reaches the tray verbatim.
fn parse_chord(chord: &str) -> Result<Shortcut, String> {
    chord.parse::<Shortcut>().map_err(|e| e.to_string())
}

/// The tray line for a quick-entry shortcut that did not register, or `None` when it did (or was
/// not asked for).
fn quick_entry_problem(d: &Dialogo, chord: &str, registered: &Result<(), String>) -> Option<String> {
    registered.as_ref().err().map(|why| {
        d.atalho_falhou.replace("{k}", &chord_label(chord)).replace("{e}", why)
    })
}

/// What pressing the quick-entry chord does to the page: go to the Code screen, whose composer takes
/// the focus on its own. A fixed string — nothing from outside is ever spliced into it — run by
/// this process in the page; the page gains no way to call back.
const QUICK_ENTRY_SCRIPT: &str = "window.location.hash = '#/code'";

fn open_quick_entry(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval(QUICK_ENTRY_SCRIPT);
    }
    bring_forward(app);
}

/// Register — or drop — the quick-entry chord to match the preferences, and report what happened.
///
/// Registration fails for ordinary reasons: another program already holds the chord, or the file
/// names one that does not parse. Either is put in the tray menu with the chord and the reason,
/// because a shortcut that silently does nothing reads as a broken app.
fn apply_quick_entry(app: &tauri::AppHandle) {
    let Some(shell) = app.try_state::<Arc<Shell>>() else { return };
    let prefs = shell.prefs();
    let shortcuts = app.global_shortcut();
    // This app registers one chord and only this function touches it, so "all" is that one.
    let _ = shortcuts.unregister_all();
    let registered = if prefs.quick_entry {
        parse_chord(&prefs.quick_entry_chord).and_then(|shortcut| {
            shortcuts
                .on_shortcut(shortcut, |app, _shortcut, event| {
                    if event.state == ShortcutState::Pressed {
                        open_quick_entry(app);
                    }
                })
                .map_err(|e| e.to_string())
        })
    } else {
        Ok(())
    };
    let d = dialogo();
    if let Some(tray) = app.try_state::<Tray>() {
        let _ = tray.quick_entry.set_text(d.atalho.replace("{k}", &chord_label(&prefs.quick_entry_chord)));
        let _ = tray.quick_entry.set_checked(prefs.quick_entry && registered.is_ok());
    }
    let problem = quick_entry_problem(d, &prefs.quick_entry_chord, &registered);
    set_problem(app, |p| &mut p.shortcut, problem);
}

/// How often the watcher looks. Three seconds is the study's number: an approval refuses itself
/// after 300, so the owner hears about it with almost all of that left.
const WATCH_TICK: Duration = Duration::from_secs(3);

/// How often the tooltip is refreshed while the app lives in the tray. `/api/usage` reads the whole
/// usage log, which is not a thing to do every three seconds for a line nobody may be hovering.
const TOOLTIP_EVERY: Duration = Duration::from_secs(30);

/// The tray's tooltip: the app's name, then what is known. A part that could not be read is left
/// out rather than shown as zero.
fn tooltip(d: &Dialogo, running: Option<usize>, spent: Option<f64>, waiting: Option<usize>) -> String {
    let mut parts = vec!["Chimera".to_string()];
    if let Some(n) = running.filter(|n| *n > 0) {
        parts.push(d.dica_rodando.replace("{n}", &n.to_string()));
    }
    if let Some(usd) = spent {
        parts.push(d.dica_gasto.replace("{usd}", &format!("{usd:.2}")));
    }
    if let Some(n) = waiting.filter(|n| *n > 0) {
        parts.push(d.dica_aguardando.replace("{n}", &n.to_string()));
    }
    parts.join(" · ")
}

/// The line for a backend that refused this shell. The fix is the token, so the message names it.
fn backend_refused(d: &Dialogo) -> String {
    d.sem_acesso.replace("{e}", &format!("HTTP 401, {}", sidecar_http::TOKEN_VAR))
}

/// Whether the tray should be saying "no access to the backend" after this tick.
///
/// A tick that asked something knows. One that asked nothing keeps what was known — the window
/// being in front says nothing about the token — unless nothing is WANTED any more (both switches
/// off), in which case there is no access left to report on and the line goes.
fn still_refused(asked: bool, refused_now: bool, refused_before: bool, wanted: bool) -> bool {
    if asked {
        refused_now
    } else {
        refused_before && wanted
    }
}

/// What one read of the approval list asks the tray to do.
#[derive(Debug, PartialEq, Eq)]
struct OnApprovals {
    /// Ask for the owner's attention: a question nobody has been called for yet, and the flash on.
    flash: bool,
    /// Rewrite the tooltip now rather than at the next 30-second tick: the waiting set changed while
    /// the app lives in the tray.
    retip: bool,
}

/// The rule for one read, pure so the tests can hold it.
///
/// `retip` is the half that matters when the window is HIDDEN. `request_user_attention` acts on the
/// window's taskbar button, and a hidden window has none: in tray mode the flash reaches nothing.
/// That is the mode where an unseen approval is most likely to refuse itself at 300 s, so the
/// tray's own tooltip is brought up to date the moment the set changes, not half a minute later.
fn on_approvals(seen: &[String], now: &[String], call_attention: bool, keep_in_tray: bool) -> OnApprovals {
    OnApprovals {
        flash: call_attention && sidecar_http::has_new(seen, now),
        retip: keep_in_tray && sidecar_http::changed(seen, now),
    }
}

/// Watch the backend for the two things the tray shows: approvals waiting (to flash the taskbar
/// while the window is not in front — P2.4) and, while the app lives in the tray, the tooltip
/// (P2.2). A thread of its own, for the reason the supervisor gives: it sleeps and blocks on I/O.
///
/// The flash only reaches a window that is on the taskbar. With "Keep in tray" on and the window
/// closed (hidden), the only signal is the tooltip — which is why the approval list is read in that
/// mode even with the flash off, and why a change in it rewrites the tooltip at once.
///
/// A backend that is down or restarting is NOT reported from here: the supervisor owns that, and
/// the window says it. A 401 is, every time, because nothing else would.
fn watch_for_the_tray(app: tauri::AppHandle, sidecar: Arc<Sidecar>, origin: Arc<Mutex<String>>, shell: Arc<Shell>) {
    let d = dialogo();
    // The waiting questions as last read: what has been flashed for, and what the tooltip shows.
    let mut seen: Vec<String> = Vec::new();
    let mut waiting: Option<usize> = None;
    // The last running count and spend read, kept so an approval-driven rewrite of the tooltip does
    // not re-read the whole usage log.
    let mut running: Option<usize> = None;
    let mut spent: Option<f64> = None;
    let mut last_tooltip: Option<Instant> = None;
    let mut refused_before = false;
    loop {
        std::thread::sleep(WATCH_TICK);
        if sidecar.stopping.load(Ordering::SeqCst) {
            return;
        }
        // A switch the Settings screen flipped, taken in on the main thread — it may re-register
        // the global chord. Posted rather than waited for: this tick reads what is in force now,
        // and the next one reads the change.
        let syncing = app.clone();
        let _ = app.run_on_main_thread(move || sync_prefs_from_file(&syncing));
        let prefs = shell.prefs();
        let origin = origin.lock().map(|o| o.clone()).unwrap_or_default();
        let token = sidecar_http::token_now();
        let token = token.as_deref();
        let mut refused = false;
        // Whether this tick asked the backend anything. A tick that asked nothing (the window is in
        // front, the tooltip is not due) has learned nothing about access and must not clear — or
        // re-announce — a 401 seen a moment ago.
        let mut asked = false;
        let mut retip = false;

        let unfocused = !shell.focused.load(Ordering::SeqCst);
        if unfocused && (prefs.call_attention || prefs.keep_in_tray) {
            asked = true;
            match sidecar_http::get_json(&origin, "/api/approvals", token, Duration::from_secs(3)) {
                Fetch::Json(list) => {
                    let ids = sidecar_http::pending_ids(&list);
                    let todo = on_approvals(&seen, &ids, prefs.call_attention, prefs.keep_in_tray);
                    if todo.flash {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.request_user_attention(Some(UserAttentionType::Informational));
                        }
                    }
                    retip = todo.retip;
                    waiting = Some(ids.len());
                    seen = ids;
                }
                Fetch::Unauthorized => {
                    refused = true;
                    waiting = None;
                }
                Fetch::Failed(_) => waiting = None,
            }
        } else if !unfocused {
            // The person is looking at the window, which shows the questions itself; a count kept
            // from before would be stale, so the tooltip leaves it out until the next read.
            waiting = None;
        }

        if prefs.keep_in_tray {
            if last_tooltip.is_none_or(|t| t.elapsed() >= TOOLTIP_EVERY) {
                last_tooltip = Some(Instant::now());
                asked = true;
                let look = |path: &str| sidecar_http::get_json(&origin, path, token, Duration::from_secs(5));
                let (turns, usage) = (look("/api/code/turns/running"), look("/api/usage"));
                refused |= matches!(turns, Fetch::Unauthorized) || matches!(usage, Fetch::Unauthorized);
                running = match &turns { Fetch::Json(v) => sidecar_http::running_count(v), _ => None };
                spent = match &usage {
                    Fetch::Json(v) => sidecar_http::spent_on(v, &sidecar_http::utc_today()),
                    _ => None,
                };
                retip = true;
            }
            if retip {
                if let Some(tray) = app.tray_by_id("main") {
                    let _ = tray.set_tooltip(Some(tooltip(d, running, spent, waiting)));
                }
            }
        } else if last_tooltip.take().is_some() {
            if let Some(tray) = app.tray_by_id("main") {
                let _ = tray.set_tooltip(Some("Chimera"));
            }
        }

        let refused = still_refused(asked, refused, refused_before, prefs.call_attention || prefs.keep_in_tray);
        // Only a CHANGE is written to stderr — the tray line is the record a person sees, and a
        // 401 every three seconds in a log is a log nobody reads.
        if refused != refused_before {
            eprintln!(
                "chimera tray: {}",
                if refused { backend_refused(d) } else { "backend access restored".to_string() }
            );
            set_problem(&app, |p| &mut p.backend, refused.then(|| backend_refused(d)));
            refused_before = refused;
        }
    }
}

fn main() {
    // A sign-in launch starts hidden in the tray and stands where a shortcut launch stands — see
    // `root_at_install_dir`. Decided before anything else, because the backend inherits the CWD.
    let autostarted = launched_by_autostart(std::env::args());
    if autostarted {
        root_at_install_dir();
    }
    tauri::Builder::default()
        // First, as the plugin requires: a second launch must hand over before anything else runs,
        // the backend above all. Two backends on one data folder each believed they were alone
        // (R12 of the review of 2026-09-30), and the backend now refuses the second one.
        //
        // It also brings back a window that "Keep in tray" hid: `bring_forward` shows it.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| bring_forward(app)))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        // Registered always, used only when the owner turns them on from the tray. Neither adds a
        // permission to the capability file, so their webview commands stay out of the page's reach.
        .plugin(tauri_plugin_autostart::Builder::new().arg(AUTOSTART_ARG).build())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(move |app| {
            // Bring up the backend, then open the window at its origin.
            //
            // The failure branch is the point. This used to be a bare `?`, which propagated to the
            // `.expect()` on `build()` — and under `windows_subsystem = "windows"` a panic writes to
            // a stderr that does not exist. Clicking the icon did nothing at all: no window, no
            // console, no file. "Nothing happens" is the least diagnosable failure a desktop app can
            // have, and it was ours on the platform this project is developed on.
            let started = resolve_paths(app).and_then(|paths| {
                let wanted = remembered_port(&memo_path(&paths.data_dir));
                start_sidecar(&paths, wanted, Budget::default()).map(|backend| (paths, backend))
            });
            let (paths, backend) = match started {
                Ok(pair) => pair,
                Err(why) => {
                    // Blocking, so the process cannot exit before the user has read it.
                    let d = dialogo();
                    app.dialog()
                        .message(format!("{}\n\n{why}", d.nao_iniciou))
                        .title(d.nao_iniciou_titulo)
                        .buttons(MessageDialogButtons::Ok)
                        .blocking_show();
                    return Err(Box::<dyn std::error::Error>::from(why));
                }
            };
            let url = backend.url.clone();
            let sidecar = Arc::new(Sidecar {
                backend: Mutex::new(Some(backend)),
                stopping: AtomicBool::new(false),
            });
            app.manage(Arc::clone(&sidecar));

            // The origin a panel window may load. Shared with the supervisor, which moves it when the
            // backend comes back on another port.
            let float_origin = Arc::new(Mutex::new(url.clone()));
            let allowed = Arc::clone(&float_origin);
            let opener = app.handle().clone();

            // The tray's preferences. A file that would not read is reported in the tray below,
            // and the defaults — today's behaviour — stand in for it meanwhile.
            let (loaded, prefs_unreadable) = prefs::load(&paths.data_dir);
            let shell = Arc::new(Shell {
                data_dir: paths.data_dir.clone(),
                prefs: Mutex::new(loaded),
                // Not in front until the window SAYS so. Starting at "focused" on a normal launch
                // was wrong whenever Windows' focus-stealing prevention opened the window behind
                // another one: no Focused(true) ever came, so no Focused(false) either, and the
                // watcher never looked until the person had activated and left the window once.
                // Corrected from the window itself just after it is built, below.
                focused: AtomicBool::new(false),
                problems: Mutex::new(Problems {
                    prefs: prefs_unreadable.map(|e| prefs_problem(dialogo(), &paths.data_dir, &e)),
                    ..Problems::default()
                }),
                prefs_set_aside: Mutex::new(None),
                // Unset, so the watcher's first look takes the file in once more: a sign-in request
                // the screen wrote just before the app last closed is carried out then.
                prefs_seen: Mutex::new(None),
            });
            app.manage(Arc::clone(&shell));

            let main_window = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse()?))
                .title("Chimera")
                .inner_size(1200.0, 800.0)
                .min_inner_size(760.0, 520.0)
                // A sign-in launch comes up in the tray: a window opening by itself at every login
                // is what makes people turn the feature off again.
                .visible(!autostarted)
                .on_new_window(move |target, features| {
                    let origin = allowed.lock().map(|origin| origin.clone()).unwrap_or_default();
                    if !is_float_url(&target, &origin) {
                        // An external link goes to the system browser; the page itself opens nothing.
                        if is_external_url(&target, &origin) {
                            let _ = open::that_detached(target.as_str());
                        }
                        return NewWindowResponse::Deny;
                    }
                    let n = FLOAT_WINDOWS.fetch_add(1, Ordering::Relaxed);
                    // `window_features` puts the new webview in the opener's environment, which
                    // WebView2 requires to hand it over; it also carries the size the page asked for.
                    match WebviewWindowBuilder::new(&opener, format!("{FLOAT_LABEL}{n}"), WebviewUrl::External(target))
                        .window_features(features)
                        .title("Chimera")
                        // The page names its panel ("Tools · Chimera"); the taskbar should too,
                        // or every panel window reads as a second copy of the app.
                        .on_document_title_changed(|window, title| {
                            let _ = window.set_title(&title);
                        })
                        .min_inner_size(280.0, 200.0)
                        .build()
                    {
                        Ok(window) => NewWindowResponse::Create { window },
                        Err(_) => NewWindowResponse::Deny,
                    }
                })
                .build()?;

            // Closing the window: hide it when the owner chose the tray, close it otherwise.
            //
            // Hiding is `prevent_close` + `hide`, and nothing else. In particular it does NOT reach
            // `kill_sidecar` — that runs from the exit hook, which a prevented close never fires —
            // so the backend, its scheduled jobs and its runs carry on. The panel windows close
            // with it, as they do when the window really closes: the app has gone to the tray, and
            // a floating panel left on screen would be the app half-open.
            let closing = Arc::clone(&shell);
            let hiding = main_window.clone();
            let floats_of = app.handle().clone();
            main_window.on_window_event(move |event| match event {
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    if prefs::on_close(&closing.prefs()) == OnClose::Hide {
                        api.prevent_close();
                        let _ = hiding.hide();
                        closing.focused.store(false, Ordering::SeqCst);
                        close_floats(&floats_of);
                    }
                }
                tauri::WindowEvent::Focused(focused) => closing.focused.store(*focused, Ordering::SeqCst),
                _ => {}
            });
            // Read the truth once the handler is in place, so a Focused event that fired between
            // build() and the handler is not lost. `fetch_or`, not `store`: an event the handler
            // has just stored wins over this read, never the other way round.
            shell
                .focused
                .fetch_or(main_window.is_focused().unwrap_or(false), Ordering::SeqCst);

            // Tray: check for updates, and quit (which kills the sidecar via the exit hook below).
            //
            // The update item is the only way to ASK. The automatic check runs once at startup and
            // stays silent when there is nothing — deliberately, so it never nags — which left the
            // user with no way to find out except launching again tomorrow.
            //
            // Above them, the switches of study 29. Each check item starts at the state that is
            // actually in force: the file for three of them, the OS for the sign-in entry.
            let d = dialogo();
            let prefs_now = shell.prefs();
            let abrir = MenuItem::with_id(app, "open", d.abrir, true, None::<&str>)?;
            let manter = CheckMenuItem::with_id(app, "keep_in_tray", d.manter_na_bandeja, true, prefs_now.keep_in_tray, None::<&str>)?;
            let iniciar = CheckMenuItem::with_id(app, "autostart", d.iniciar_com_sistema, true, app.autolaunch().is_enabled().unwrap_or(false), None::<&str>)?;
            let chamar = CheckMenuItem::with_id(app, "attention", d.chamar_atencao, true, prefs_now.call_attention, None::<&str>)?;
            let atalho = CheckMenuItem::with_id(app, "quick_entry", d.atalho.replace("{k}", &chord_label(&prefs_now.quick_entry_chord)), true, false, None::<&str>)?;
            let status = MenuItem::with_id(app, "status", "", false, None::<&str>)?;
            let atualizar = MenuItem::with_id(app, "update", dialogo().menu, true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", dialogo().sair, true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &abrir,
                    &PredefinedMenuItem::separator(app)?,
                    &manter,
                    &iniciar,
                    &chamar,
                    &atalho,
                    &PredefinedMenuItem::separator(app)?,
                    &atualizar,
                    &quit,
                ],
            )?;
            let icon = app.default_window_icon().cloned();
            // An id, so the watcher can find the tray again to write its tooltip.
            let mut tray = TrayIconBuilder::with_id("main").menu(&menu).tooltip("Chimera");
            if let Some(icon) = icon {
                tray = tray.icon(icon);
            }
            app.manage(Tray {
                menu: menu.clone(),
                status,
                status_shown: AtomicBool::new(false),
                autostart: iniciar.clone(),
                quick_entry: atalho.clone(),
                keep_in_tray: manter.clone(),
                attention: chamar.clone(),
            });
            // muda ticks a check item before the event arrives, so `is_checked` IS the new wish.
            let keep = manter.clone();
            let attention = chamar.clone();
            let wish = iniciar.clone();
            let quick = atalho.clone();
            tray.on_menu_event(move |app, event| match event.id.as_ref() {
                "quit" => app.exit(0),
                "open" => bring_forward(app),
                "keep_in_tray" => {
                    let on = keep.is_checked().unwrap_or(false);
                    change_prefs(app, |p| p.keep_in_tray = on);
                }
                "attention" => {
                    let on = attention.is_checked().unwrap_or(true);
                    change_prefs(app, |p| p.call_attention = on);
                }
                "autostart" => apply_autostart(app, wish.is_checked().unwrap_or(false)),
                "quick_entry" => {
                    let on = quick.is_checked().unwrap_or(false);
                    change_prefs(app, |p| p.quick_entry = on);
                    apply_quick_entry(app);
                }
                "update" => {
                    // Spawned, not awaited: this handler runs on the UI thread and the check does
                    // network I/O. Blocking here freezes the window while GitHub is asked.
                    let handle = app.clone();
                    tauri::async_runtime::spawn(async move {
                        if let Err(erro) = check_for_update(handle.clone(), true).await {
                            // A check the user asked for reports its own failure. The startup one
                            // swallows this same error on purpose; here, saying nothing would be
                            // indistinguishable from "you are up to date".
                            let d = dialogo();
                            handle
                                .dialog()
                                .message(d.falhou.replace("{e}", &erro.to_string()))
                                .title(d.titulo)
                                .buttons(MessageDialogButtons::Ok)
                                .blocking_show();
                        }
                    });
                }
                _ => {}
            })
            .build(app)?;

            // Watch the backend for the rest of the session, on its own thread.
            //
            // A thread rather than the async runtime: the loop sleeps, blocks on a TCP connect and
            // can block for a whole start budget, none of which belongs on an executor shared with
            // the update check.
            let supervisor = app.handle().clone();
            let watched = paths.clone();
            let mut showing = url.clone();
            let moved_origin = Arc::clone(&float_origin);
            std::thread::spawn(move || {
                supervise(sidecar, watched, Tuning::default(), dialogo(), move |event| match event {
                    Supervised::Restarted(fresh) => {
                        // Same origin is the normal case and needs nothing: the page is still
                        // loaded, and its next poll simply succeeds. A DIFFERENT origin means the
                        // backend could not have its old port back, and the loaded page is now
                        // talking to nothing — so move the window, which costs a reload and the
                        // per-origin `localStorage` behind it, and is still the only way back.
                        if fresh != showing {
                            // Panel windows point at the old port; they close and their panels return.
                            if let Ok(mut origin) = moved_origin.lock() {
                                origin.clone_from(&fresh);
                            }
                            close_floats(&supervisor);
                            if let (Some(window), Ok(target)) =
                                (supervisor.get_webview_window("main"), fresh.parse::<tauri::Url>())
                            {
                                let _ = window.navigate(target);
                            }
                            showing = fresh;
                        }
                    }
                    Supervised::GaveUp(why) => {
                        // The screen says the backend is down on its own (it polls `/api/doctor`),
                        // but only this side knows that nothing more will be tried. Blocking is safe
                        // here for the same reason as in the updater: this is not the main thread.
                        supervisor
                            .dialog()
                            .message(&why)
                            .title(dialogo().parou_titulo)
                            .buttons(MessageDialogButtons::Ok)
                            .blocking_show();
                    }
                });
            });

            // The update check: once at startup, then every few hours for as long as the app runs.
            //
            // It used to be the startup one alone, and that is a check an app like this never gets
            // to make. Chimera is left open; the launch that would have found the release happened
            // yesterday. Measured on 2026-09-02: a user sat on 0.49.0 with 0.49.1 published, saw
            // nothing, and installed it from the website by hand.
            //
            // A THREAD rather than the async runtime, for the reason the supervisor above gives
            // about itself: this loop mostly sleeps, and a sleeping task does not belong on an
            // executor shared with work that has somewhere to be. It also keeps `blocking_show`
            // honest — the dialog blocks for the answer, and this is not the main thread.
            //
            // Any error (offline, no release, rate-limit, bad signature) is swallowed, as before:
            // the check must never nag or crash. The pip/web "update signal" is separate.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let _ = tauri::async_runtime::block_on(check_for_update(handle.clone(), false));
                loop {
                    std::thread::sleep(INTERVALO_DE_CHECAGEM);
                    let _ = tauri::async_runtime::block_on(check_for_update(handle.clone(), false));
                }
            });

            // The tray's own look at the backend: the approval flash and the tooltip. After the
            // tray exists, because it writes into it; the shortcut and any startup problem too.
            apply_quick_entry(app.handle());
            show_problems(app.handle());
            let watcher = app.handle().clone();
            let watched_origin = Arc::clone(&float_origin);
            let watched_sidecar = app.state::<Arc<Sidecar>>().inner().clone();
            std::thread::spawn(move || watch_for_the_tray(watcher, watched_sidecar, watched_origin, shell));

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
                // Closing the main window closes the panel windows, so the last window closing
                // still means the app is done: without this, a floating panel kept the app (and
                // the backend) alive after the person closed the window they think of as the app.
                tauri::RunEvent::WindowEvent { label, event: tauri::WindowEvent::Destroyed, .. }
                    if label == "main" =>
                {
                    close_floats(app_handle);
                }
                // macOS: clicking the Dock icon of an app whose window "Keep in tray" hid.
                #[cfg(target_os = "macos")]
                tauri::RunEvent::Reopen { .. } => bring_forward(app_handle),
                _ => {}
            }
        });
}

#[cfg(test)]
mod float_window_tests {
    use super::*;

    const ORIGIN: &str = "http://127.0.0.1:8765";

    fn allowed(target: &str) -> bool {
        is_float_url(&tauri::Url::parse(target).expect("a url"), ORIGIN)
    }

    #[test]
    fn a_panel_address_at_this_origin_opens() {
        assert!(allowed("http://127.0.0.1:8765/?float=activity.jobs"));
        assert!(allowed("http://127.0.0.1:8765/?float=activity.tools"));
        // The origin as the port file writes it, with or without a trailing slash.
        assert!(is_float_url(&tauri::Url::parse("http://127.0.0.1:8765/?float=activity.jobs").unwrap(), "http://127.0.0.1:8765/"));
    }

    #[test]
    fn anything_else_stays_refused_as_it_was() {
        for target in [
            "https://example.com/?float=activity.jobs",
            "http://127.0.0.1:8766/?float=activity.jobs",
            "http://localhost:8765/?float=activity.jobs",
            "https://127.0.0.1:8765/?float=activity.jobs",
            "http://127.0.0.1:8765/settings?float=activity.jobs",
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/?float=",
            "http://127.0.0.1:8765/?float=activity.jobs&next=https://example.com",
            "http://127.0.0.1:8765/?float=activity.jobs#x",
            "http://127.0.0.1:8765/?float=Activity.Jobs",
            "http://127.0.0.1:8765/?float=%3Cscript%3E",
            "http://user:pw@127.0.0.1:8765/?float=activity.jobs",
            "http://127.0.0.1:8765/?other=activity.jobs",
        ] {
            assert!(!allowed(target), "{target} must be refused");
        }
    }

    #[test]
    fn a_conversation_address_at_this_origin_opens_and_nothing_near_it() {
        assert!(allowed("http://127.0.0.1:8765/?conversation=3f2a9c0d8e7b4a1f9c6d5e4b3a2f1e0d"));
        assert!(allowed("http://127.0.0.1:8765/?conversation=a-b_C9"));
        for target in [
            "http://127.0.0.1:8765/?conversation=",
            "http://127.0.0.1:8765/?conversation=..%2F..%2Fetc",
            "http://127.0.0.1:8765/?conversation=a%20b",
            "http://127.0.0.1:8765/?conversation=abc&float=activity.jobs",
            "http://127.0.0.1:8765/code?conversation=abc",
            "https://example.com/?conversation=abc",
        ] {
            assert!(!allowed(target), "{target} must be refused");
        }
        let long = format!("http://127.0.0.1:8765/?conversation={}", "a".repeat(65));
        assert!(!allowed(&long), "an id longer than the store keeps must be refused");
    }

    #[test]
    fn an_origin_that_does_not_parse_allows_nothing() {
        assert!(!is_float_url(&tauri::Url::parse("http://127.0.0.1:8765/?float=activity.jobs").unwrap(), "not a url"));
    }

    #[test]
    fn a_web_page_goes_to_the_system_browser() {
        let external = |target: &str| is_external_url(&tauri::Url::parse(target).expect("a url"), ORIGIN);
        assert!(external("https://github.com/brcampidelli/chimera-agent"));
        assert!(external("http://example.com/docs?page=2#install"));
        // A local development server is a web page too, as long as it is not this app.
        assert!(external("http://localhost:3000/"));
        assert!(external("http://127.0.0.1:8766/"));
    }

    #[test]
    fn nothing_else_leaves_the_app() {
        let external = |target: &str| is_external_url(&tauri::Url::parse(target).expect("a url"), ORIGIN);
        for target in [
            // The app's own pages: a panel window or nothing, never a browser tab.
            "http://127.0.0.1:8765/",
            "http://127.0.0.1:8765/settings",
            "http://127.0.0.1:8765/?float=activity.jobs",
            // Schemes that would have the OS open or run something other than a web page.
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "ms-settings:privacy",
            "mailto:someone@example.com",
            "ftp://example.com/file",
            // Credentials in the address.
            "https://user:secret@example.com/",
        ] {
            assert!(!external(target), "{target} must not reach the system browser");
        }
        assert!(!is_external_url(&tauri::Url::parse("https://example.com/").unwrap(), "not a url"));
    }

    /// The panel windows are no stronger than the main one only while the capability file grants to
    /// `main` alone. A glob there ("*", "float-*") would hand IPC to every window this code opens.
    #[test]
    fn the_capability_file_grants_to_the_main_window_alone() {
        let text = include_str!("../capabilities/default.json");
        let json: serde_json::Value = serde_json::from_str(text).expect("the capability file parses");
        assert_eq!(json["windows"], serde_json::json!(["main"]));
        assert!(json.get("webviews").is_none(), "a webview grant would reach the panel windows");
        assert!(!FLOAT_LABEL.starts_with("main"));
    }
}

#[cfg(test)]
mod tray_tests {
    //! The tray's switches (study 29: P2.2, P2.3, P2.4, P2.6).
    //!
    //! What can run without an app runs for real: the chord parser, the tooltip, the problem line,
    //! the launch-argument check. What cannot — `main`'s setup closure needs a `tauri::App` — is read
    //! from the source, with every needle assembled by `concat!` so this module never contains the
    //! text it searches for, and over a window that excludes EVERY test module in the file (the
    //! older `producao()` resumes after the first one and so still sees this module and the float
    //! tests, which is a window that can contain its own answer).
    use super::*;

    /// main.rs with every `#[cfg(test)] mod … { … }` block cut out.
    fn production_only() -> String {
        let mut rest = include_str!("main.rs").replace("\r\n", "\n");
        let attribute = concat!("#[cfg(", "test)]");
        while let Some(start) = rest.find(attribute) {
            let end = rest[start..].find("\n}\n").map_or(rest.len(), |i| start + i + 3);
            rest.replace_range(start..end, "");
        }
        rest
    }

    /// The body of the first function named `name`, up to the next top-level item.
    fn body_of(source: &str, signature: &str) -> String {
        let after = source
            .split_once(signature)
            .unwrap_or_else(|| panic!("{signature} exists"))
            .1;
        after.split_once("\n}\n").map_or(after, |(body, _)| body).to_string()
    }

    /// P2.2's own measure, from the study: with the option on, CloseRequested does not end the
    /// backend. The handler consults the rule, prevents the close and hides — and nothing in it
    /// reaches the function that kills the sidecar.
    #[test]
    fn closing_the_window_hides_it_when_asked_and_never_kills_the_backend() {
        let source = production_only();
        let handler = source
            .split_once(concat!("tauri::WindowEvent::Close", "Requested { api, .. } =>"))
            .expect("the main window intercepts its close")
            .1;
        let handler = handler
            .split_once(concat!("tauri::WindowEvent::Foc", "used("))
            .map_or(handler, |(arm, _)| arm);
        for step in [
            concat!("prefs::on_", "close(&closing.prefs()) == OnClose::Hide"),
            concat!("api.prevent_", "close()"),
            concat!("hiding.", "hide()"),
        ] {
            assert!(handler.contains(step), "the close handler no longer does {step}");
        }
        assert!(
            !handler.contains(concat!("kill_", "sidecar")),
            "hiding the window reaches kill_sidecar — the backend would die with the window again"
        );
        // And the rule it consults is the one tested in prefs.rs: hide only when asked.
        assert_eq!(prefs::on_close(&Prefs::default()), OnClose::Close);
        assert_eq!(prefs::on_close(&Prefs { keep_in_tray: true, ..Prefs::default() }), OnClose::Hide);
        // Quit still ends everything: the exit hook still kills the sidecar.
        let run = source.rsplit_once(concat!(".run(|app_handle, ", "event|")).expect("the run hook").1;
        assert!(run.contains(concat!("kill_sidecar(app_", "handle)")), "Quit stopped killing the backend");
    }

    #[test]
    fn a_sign_in_launch_is_recognised_by_its_argument_alone() {
        let args = |v: &[&str]| v.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        assert!(launched_by_autostart(args(&["chimera-desktop.exe", "--autostart"])));
        assert!(!launched_by_autostart(args(&["chimera-desktop.exe"])));
        // argv[0] is the program, not an argument — an exe renamed to the flag is not a sign-in.
        assert!(!launched_by_autostart(args(&["--autostart"])));
    }

    /// The entry the plugin writes and the flag main reads are the same constant, and a sign-in
    /// launch starts hidden and in the install directory (where the backend's `.env` lives).
    #[test]
    fn a_sign_in_launch_starts_hidden_where_its_keys_are() {
        let source = production_only();
        assert!(source.contains(concat!("Builder::new().arg(AUTO", "START_ARG)")));
        let main = body_of(&source, concat!("fn ", "main() {"));
        assert!(main.contains(concat!("if autostarted {\n        root_at_install_", "dir();")));
        assert!(main.contains(concat!(".visible(!auto", "started)")), "a sign-in launch opens a window");
    }

    #[test]
    fn a_chord_that_does_not_parse_is_reported_with_its_reason() {
        assert!(parse_chord(prefs::DEFAULT_CHORD).is_ok(), "the default chord must register");
        let refused = parse_chord("Ctrl+Nope");
        assert!(refused.is_err());
        let d = &DIALOGO[0];
        let line = quick_entry_problem(d, "Ctrl+Nope", &refused.map(|_| ())).expect("a line");
        assert!(line.contains("Ctrl+Nope"), "the line does not name the chord: {line}");
        assert!(!line.contains("{k}") && !line.contains("{e}"), "{line}");
        // A chord another program holds is the same path with the OS's words.
        let taken = quick_entry_problem(d, prefs::DEFAULT_CHORD, &Err("HotKey already registered".into()))
            .expect("a line");
        assert!(taken.contains("Ctrl+Shift+Space") && taken.contains("already registered"), "{taken}");
        assert_eq!(quick_entry_problem(d, prefs::DEFAULT_CHORD, &Ok(())), None);
    }

    /// The registration's result reaches the tray. The path the study asked to see tested: a
    /// failure that is computed and then dropped on the floor would pass every test above.
    #[test]
    fn a_failed_registration_is_put_in_the_tray_not_swallowed() {
        let body = body_of(&production_only(), concat!("fn apply_quick_", "entry("));
        assert!(body.contains(concat!("let registered = if prefs.quick_", "entry {")));
        assert!(body.contains(concat!("quick_entry_problem(d, &prefs.quick_entry_chord, &regis", "tered)")));
        assert!(body.contains(concat!("set_problem(app, |p| &mut p.short", "cut, problem)")));
        // And the check mark says whether it is ACTIVE, not whether it was wished for.
        assert!(body.contains(concat!("set_checked(prefs.quick_entry && registered.is_", "ok())")));
    }

    #[test]
    fn chords_read_the_way_the_keyboard_says_them() {
        let local = if cfg!(target_os = "macos") { "Cmd" } else { "Ctrl" };
        assert_eq!(chord_label("CommandOrControl+Shift+Space"), format!("{local}+Shift+Space"));
        assert_eq!(chord_label("Alt+K"), "Alt+K");
    }

    #[test]
    fn the_tooltip_leaves_out_what_it_could_not_read() {
        let d = &DIALOGO[0];
        assert_eq!(tooltip(d, None, None, None), "Chimera");
        assert_eq!(tooltip(d, Some(0), None, Some(0)), "Chimera", "zero running is not news");
        assert_eq!(
            tooltip(d, Some(2), Some(0.4199), Some(1)),
            "Chimera · 2 running · US$ 0.42 today (UTC) · 1 waiting for you"
        );
        // A spend that could not be read is absent — never "US$ 0.00".
        assert!(!tooltip(d, Some(1), None, None).contains("US$"));
    }

    #[test]
    fn every_problem_keeps_its_place_in_the_line() {
        let mut p = Problems::default();
        assert_eq!(p.line(), None, "nothing wrong, no line in the menu");
        p.shortcut = Some("a".into());
        p.backend = Some("b".into());
        assert_eq!(p.line().as_deref(), Some("a · b"));
        p.shortcut = None;
        assert_eq!(p.line().as_deref(), Some("b"), "clearing one source erased another");
        assert!(backend_refused(d0()).contains(sidecar_http::TOKEN_VAR), "the 401 line must name the fix");
    }

    #[test]
    fn a_tick_that_asked_nothing_does_not_forget_a_refusal() {
        assert!(still_refused(true, true, false, true), "a 401 now is a 401");
        assert!(!still_refused(true, false, true, true), "an answer now clears it");
        assert!(still_refused(false, false, true, true), "the window came forward and the 401 vanished");
        assert!(!still_refused(false, false, true, false), "nothing wanted, nothing to report");
        assert!(!still_refused(false, false, false, true));
    }

    fn d0() -> &'static Dialogo {
        &DIALOGO[0]
    }

    /// P2.4 is wired: the watcher runs, flashes only while the window is not in front, asks for the
    /// informational kind (the taskbar, not a modal), and a 401 is reported rather than read as zero.
    #[test]
    fn the_watcher_runs_and_a_refusal_is_said_out_loud() {
        let source = production_only();
        let main = body_of(&source, concat!("fn ", "main() {"));
        assert!(main.contains(concat!("watch_for_the_", "tray(watcher")), "the watcher is never started");
        let watch = body_of(&source, concat!("fn watch_for_the_", "tray("));
        for needle in [
            concat!("let unfocused = !shell.focused.", "load("),
            concat!("if unfocused && (prefs.call_attention || prefs.keep_in_", "tray)"),
            concat!("request_user_attention(Some(UserAttentionType::Inform", "ational))"),
            concat!("on_approvals(&seen, &ids, prefs.call_attention, prefs.keep_in_", "tray)"),
            concat!("if todo.fla", "sh {"),
            concat!("retip = todo.re", "tip;"),
            concat!("if retip {
                if let Some(tray) = app.tray_by_", "id(\"main\")"),
            concat!("Fetch::Unauthorized => {
                    refused = ", "true;"),
            concat!("set_problem(&app, |p| &mut p.back", "end"),
            concat!("still_refused(asked, refused, refused_", "before"),
            concat!("sidecar_http::token_", "now()"),
        ] {
            assert!(watch.contains(needle), "the watcher no longer does {needle}");
        }
    }

    /// The file has two writers — the tray and the Settings screen — so the shell takes it in while
    /// it runs, not once at startup: the watcher posts the look every tick, a tray click looks
    /// before it saves the whole file (or it would put back a switch the screen just changed), and
    /// the report the screen reads is written wherever the tray's line changes.
    #[test]
    fn a_switch_saved_from_the_settings_screen_is_taken_in_while_the_app_runs() {
        let source = production_only();
        let watch = body_of(&source, concat!("fn watch_for_the_", "tray("));
        assert!(
            watch.contains(concat!("run_on_main_thread(move || sync_prefs_from_", "file(&syncing))")),
            "the watcher no longer looks at the preferences file"
        );
        let change = body_of(&source, concat!("fn change_", "prefs("));
        let look = change.find(concat!("sync_prefs_from_", "file(app);")).expect("change_prefs looks first");
        let save = change.find(concat!("prefs::", "save(")).expect("change_prefs saves");
        assert!(look < save, "a tray click saves before taking in the screen's change");
        let shown = body_of(&source, concat!("fn show_", "problems("));
        assert!(shown.contains(concat!("prefs::save_", "state(")), "the screen's report is no longer written");
        let sync = body_of(&source, concat!("fn sync_prefs_from_", "file("));
        assert!(sync.contains(concat!("apply_auto", "start(app, wanted)")), "a sign-in request is not carried out");
        assert!(sync.contains(concat!("apply_quick_", "entry(app)")), "a changed chord is not registered again");
    }

    /// The rule behind one read of the approval list. In tray mode the window is hidden and the
    /// flash reaches nothing, so a change in the waiting set must rewrite the tooltip at once —
    /// with the flash on or off — and the flash must still be once per question.
    #[test]
    fn in_the_tray_a_change_in_approvals_rewrites_the_tooltip_at_once() {
        let ids = |v: &[&str]| v.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        let (none, a) = (ids(&[]), ids(&["a"]));
        assert_eq!(on_approvals(&none, &a, true, true), OnApprovals { flash: true, retip: true });
        assert_eq!(
            on_approvals(&none, &a, false, true),
            OnApprovals { flash: false, retip: true },
            "with the flash off, the tray is the only signal left"
        );
        assert_eq!(on_approvals(&none, &a, true, false), OnApprovals { flash: true, retip: false });
        assert_eq!(on_approvals(&a, &a, true, true), OnApprovals { flash: false, retip: false }, "same question, no news");
        assert_eq!(
            on_approvals(&a, &none, true, true),
            OnApprovals { flash: false, retip: true },
            "an answered question must leave the tooltip, not linger 30 s"
        );
    }

    /// The window starts as NOT focused and is corrected from the window itself, so a window that
    /// focus-stealing prevention opened in the background is watched from the first tick.
    #[test]
    fn focus_is_read_from_the_window_not_assumed_at_launch() {
        let main = body_of(&production_only(), concat!("fn ", "main() {"));
        assert!(main.contains(concat!("focused: AtomicBool::new(fal", "se),")), "focus is assumed again");
        assert!(main.contains(concat!(".fetch_or(main_window.is_focu", "sed().unwrap_or(false)")));
    }

    /// A broken prefs file moved aside before the save is said in the tray with where it went, and
    /// stays said: the next successful save must not take the line away.
    #[test]
    fn a_set_aside_prefs_file_is_said_and_stays_said() {
        let body = body_of(&production_only(), concat!("fn change_", "prefs("));
        assert!(body.contains(concat!("Ok(Some(aside)) => {")));
        assert!(body.contains(concat!("prefs_guardado.replace(\"{f}\", &aside.dis", "play()")));
        assert!(body.contains(concat!("Ok(None) => shell.prefs_set_aside.lock()")));
        let line = DIALOGO[0].prefs_guardado.replace("{f}", "C:/x/shell-prefs.json.bad");
        assert!(line.contains("shell-prefs.json.bad") && !line.contains("{f}"), "{line}");
    }

    /// The webview gains nothing: no permission for either plugin is granted to any window.
    #[test]
    fn the_new_plugins_add_no_permission_to_the_page() {
        let text = include_str!("../capabilities/default.json");
        let json: serde_json::Value = serde_json::from_str(text).expect("the capability file parses");
        let permissions = json["permissions"].as_array().expect("a permission list");
        for granted in permissions {
            let name = granted.as_str().unwrap_or_default();
            assert!(
                !name.starts_with("autostart") && !name.starts_with("global-shortcut"),
                "{name} hands the page a plugin this shell drives from Rust only"
            );
        }
    }
}
