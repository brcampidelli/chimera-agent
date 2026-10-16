//! The app and the background service (S1, PR 3 + PR 4, `study/opencode-v2/S1-SPEC.md`).
//!
//! Until now the shell always OWNED its backend: a child, started at launch, killed at exit. With
//! the background service on (the owner's default since 2026-10-16) the backend is
//! `chimera-backend service run` instead — detached, published in `~/.chimera/service-<channel>.json`,
//! alive after the window and the app are gone — and the shell ATTACHES to it. Which of the two a
//! launch gets is [`ensure_backend`]'s whole job, decided from the record and from what the server
//! says about itself (`GET /api/info`), never from the file alone:
//!
//! | what is there                                   | what the shell does                         |
//! |-------------------------------------------------|---------------------------------------------|
//! | no record, or a dead one                         | starts one (service, or an owned child)     |
//! | a live service, same home, same or newer version | attaches; same protocol required if newer   |
//! | a live service, older version                    | stops it with drain, then starts the new one|
//! | a live service of another protocol               | a clear error — neither can drive the other |
//! | a live service of another data folder            | a private `--standalone` child, and a notice|
//!
//! **What the shell may do to each.** [`BackendHandle::Owned`] is its child: killed at exit, as
//! always. [`BackendHandle::Attached`] is not: the shell never kills it, at exit or anywhere else;
//! it asks it to stop (`POST /api/service/stop?drain=1`) when the owner says so, or when a newer app
//! replaces it.
//!
//! **The screen credential (#950).** A service holds none of its own, so an attached window's
//! one-click approvals need it LEASED to the service, by the shell that started it, with the
//! launcher key it handed that service in the child's environment (`chimera/api/service_windows.py`).
//! See [`Attached::launched_here`].

use std::cmp::Ordering;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::sidecar_http;
use crate::Backend;

/// Bumped only when a client of the previous protocol could no longer drive the server. The same
/// number as `chimera/core/service_record.py`'s `PROTOCOL`.
pub const PROTOCOL: i64 = 1;
/// The variables the shell hands the service it spawns. Mirrors of the backend's names.
pub const LAUNCHER_ENV: &str = "CHIMERA_SCREEN_LAUNCHER_TOKEN";
pub const LAUNCHER_HEADER: &str = "X-Chimera-Launcher";
/// The service's executable name inside its own copy (PR 4) — distinct from the app's child, so the
/// installer's `taskkill /IM chimera-backend.exe` never takes the owner's service with it.
pub const SERVICE_EXE: &str = if cfg!(windows) { "chimera-service.exe" } else { "chimera-service" };
/// Where the copies live, under the app's data directory.
pub const SERVICE_BIN: &str = "service-bin";
const COMPLETE: &str = ".complete";

/// `~/.chimera/service-<channel>.json`, as the backend writes it (`ServiceRecord`).
#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct Record {
    pub id: String,
    pub version: String,
    pub protocol: i64,
    pub url: String,
    pub pid: u32,
    pub token: String,
    pub home: String,
    pub mode: String,
    pub started_at: f64,
}

/// The channel a frozen build publishes under: `stable`, unless `$CHIMERA_SERVICE_CHANNEL` says.
pub fn channel() -> String {
    match std::env::var("CHIMERA_SERVICE_CHANNEL").map(|c| c.trim().to_ascii_lowercase()) {
        Ok(c) if c == "stable" || c == "dev" => c,
        _ => "stable".to_string(),
    }
}

/// `$CHIMERA_SERVICE_DIR`, or `~/.chimera` — where the backend's `service_dir()` looks.
pub fn service_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("CHIMERA_SERVICE_DIR") {
        if !dir.trim().is_empty() {
            return PathBuf::from(dir.trim());
        }
    }
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".chimera")
}

pub fn record_path(dir: &Path, channel: &str) -> PathBuf {
    dir.join(format!("service-{channel}.json"))
}

pub fn read_record(path: &Path) -> Option<Record> {
    let text = std::fs::read_to_string(path).ok()?;
    let record: Record = serde_json::from_str(&text).ok()?;
    (!record.id.is_empty() && !record.url.is_empty() && !record.token.is_empty()).then_some(record)
}

/// One spelling of a data folder, for comparing what the backend wrote (`Path.resolve()`) with
/// what this process knows. Windows: no `\\?\` prefix, backslashes, any case.
pub fn normal_home(path: &str) -> String {
    let mut p = path.trim().to_string();
    if let Some(rest) = p.strip_prefix(r"\\?\") {
        p = rest.to_string();
    }
    if cfg!(windows) {
        p = p.replace('/', "\\").to_lowercase();
    }
    while p.len() > 1 && (p.ends_with('\\') || p.ends_with('/')) && !p.ends_with(":\\") {
        p.pop();
    }
    p
}

/// Both sides are canonicalised when they exist: a Windows 8.3 short name (`RUNNER~1`, which the CI
/// runner's temp folder has) and its long name are one folder, and comparing their text called them
/// two — the app would then run `--standalone` beside its own service.
pub fn same_home(recorded: &str, ours: &Path) -> bool {
    let resolve = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let recorded = resolve(Path::new(recorded.trim()));
    normal_home(&recorded.to_string_lossy()) == normal_home(&resolve(ours).to_string_lossy())
}

/// `0.67.4` against `0.67.10`, numerically; a suffix (`+source`, `rc1`) is ignored past the
/// numbers it starts with.
pub fn compare_versions(a: &str, b: &str) -> Ordering {
    let parts = |v: &str| -> Vec<u64> {
        v.trim()
            .trim_start_matches('v')
            .split(['.', '+', '-'])
            .map_while(|p| p.parse::<u64>().ok())
            .collect()
    };
    let (a, b) = (parts(a), parts(b));
    for i in 0..a.len().max(b.len()) {
        match a.get(i).unwrap_or(&0).cmp(b.get(i).unwrap_or(&0)) {
            Ordering::Equal => continue,
            other => return other,
        }
    }
    Ordering::Equal
}

/// What a record turned out to be once its server was asked.
#[derive(Clone, Debug, PartialEq)]
pub enum Probe {
    /// The server answered `/api/info` and agrees with the record; its protocol beside it.
    Alive { protocol: i64 },
    /// The record's pid is not running: litter.
    Stale,
    /// The pid runs and nothing answers (starting, wedged, or a reused pid).
    Unreachable,
    /// It answered 401 to the record's own token.
    Unauthorized,
    /// It answered, and not what the record says.
    Incompatible(String),
}

/// What [`ensure_backend`] decided, before acting.
#[derive(Debug, PartialEq)]
pub enum Plan {
    Spawn,
    Attach,
    Replace,
    Standalone(String),
    Refuse(String),
}

/// The decision table at the top of this file, pure.
pub fn plan(record: Option<&Record>, probe: Option<&Probe>, our_version: &str, our_home: &Path) -> Plan {
    let (Some(record), Some(probe)) = (record, probe) else { return Plan::Spawn };
    match probe {
        Probe::Stale | Probe::Unreachable => Plan::Spawn,
        Probe::Unauthorized => Plan::Refuse(format!(
            "the background service at {} refused the token in its own record — stop it with \
             `chimera service stop` and open the app again",
            record.url
        )),
        Probe::Incompatible(why) => Plan::Refuse(format!(
            "the background service record at {} does not match the server ({why}) — stop it \
             with `chimera service stop` and open the app again",
            record.url
        )),
        Probe::Alive { protocol } => {
            if !same_home(&record.home, our_home) {
                return Plan::Standalone(record.home.clone());
            }
            match compare_versions(&record.version, our_version) {
                Ordering::Less => Plan::Replace,
                _ if *protocol != PROTOCOL => Plan::Refuse(format!(
                    "the background service is Chimera {} and speaks protocol {protocol}; this app \
                     ({our_version}) speaks {PROTOCOL}. Update the app, or stop the service with \
                     `chimera service stop`",
                    record.version
                )),
                _ => Plan::Attach,
            }
        }
    }
}

/// A backend the shell did not start as its child: the background service.
#[derive(Clone, Debug, PartialEq)]
pub struct Attached {
    pub url: String,
    pub pid: u32,
    pub id: String,
    pub version: String,
    /// This shell spawned it, with its launcher key: only then can it lease the window's screen
    /// credential to it — one-click approvals. A service started anywhere else (a terminal, the
    /// app's previous run) refuses the lease, and a yes there takes the question's code until the
    /// app restarts it ("Restart background service" in the tray).
    pub launched_here: bool,
    /// Where its output goes; read for the report when it dies.
    pub log: PathBuf,
}

/// The backend this shell talks to.
pub enum BackendHandle {
    /// Its own child — `app` mode, or `--standalone`. Killed at exit.
    Owned(Backend),
    /// The background service. Never killed by the shell.
    Attached(Attached),
}

impl BackendHandle {
    pub fn url(&self) -> &str {
        match self {
            Self::Owned(b) => &b.url,
            Self::Attached(a) => &a.url,
        }
    }

    #[cfg(test)]
    pub fn owned_mut(&mut self) -> Option<&mut Backend> {
        match self {
            Self::Owned(b) => Some(b),
            Self::Attached(_) => None,
        }
    }

    pub fn attached(&self) -> Option<&Attached> {
        match self {
            Self::Attached(a) => Some(a),
            Self::Owned(_) => None,
        }
    }

    /// Kill it if it is the shell's own child; an attached service is left running, always.
    pub fn kill_if_owned(&mut self) {
        if let Self::Owned(backend) = self {
            let _ = backend.child.kill();
        }
    }
}

/// What the shell wants this time.
pub struct Wish {
    /// Start the background service when a backend has to be started (the preference, unless
    /// the owner stopped the service for this session).
    pub service: bool,
    /// `--standalone` on the app's command line: a private child, whatever is published.
    pub standalone: bool,
    pub port: u16,
    pub version: String,
    pub home: PathBuf,
    /// The token in force, when one is known (the owner's, or the one this session already uses).
    pub token: Option<String>,
    /// A fresh one, for an owned child when none is known.
    pub fresh_token: String,
}

/// Something worth telling the owner about how the backend was found.
#[derive(Debug, PartialEq)]
pub enum Notice {
    /// A service of another data folder holds this channel; this app runs privately.
    OtherHome(String),
    /// An older service was replaced.
    Replaced(String),
}

pub struct Ensured {
    pub handle: BackendHandle,
    /// The bearer token the backend enforces — what the window and the tray send.
    pub token: String,
    pub notice: Option<Notice>,
}

/// Everything [`ensure_backend`] does to the world, so its tests can drive it with a double.
pub trait Services {
    fn record(&self) -> Option<Record>;
    fn probe(&self, record: &Record) -> Probe;
    /// `POST /api/service/stop?drain=…`; the number of turns and runs it waits for.
    fn stop(&self, record: &Record, drain: bool) -> Result<usize, String>;
    /// Whether the record's process is gone within `patience`.
    fn wait_gone(&self, record: &Record, patience: Duration) -> bool;
    /// Start the background service and wait for it to publish itself and answer. With it, the
    /// token its record names — the one it enforces.
    fn spawn_service(&self, port: u16, token: Option<&str>) -> Result<(Attached, String), String>;
    /// Start an owned child (`app` mode, or `--standalone`).
    fn spawn_owned(&self, port: u16, token: &str, standalone: bool) -> Result<Backend, String>;
    /// `running` turns are still going in a service about to be replaced: wait for them (true), or
    /// stop it now (false)?
    fn ask_drain(&self, running: usize) -> bool;
}

/// How long an older service is given to finish what it runs before it is replaced.
pub const DRAIN_PATIENCE: Duration = Duration::from_secs(15 * 60);
const STOP_PATIENCE: Duration = Duration::from_secs(60);
/// A record whose pid runs but whose port does not answer yet may be a service still starting.
const UNREACHABLE_PATIENCE: Duration = Duration::from_secs(15);

/// Stop an attached service: with drain, asking the owner when turns are still going. True when
/// it is gone afterwards.
pub fn stop_service(svc: &impl Services, record: &Record) -> Result<(), String> {
    let running = svc.stop(record, true)?;
    let waited = running > 0 && svc.ask_drain(running);
    if running > 0 && !waited {
        svc.stop(record, false)?;
    }
    if svc.wait_gone(record, if waited { DRAIN_PATIENCE } else { STOP_PATIENCE }) {
        Ok(())
    } else {
        Err(format!(
            "the background service (pid {}) did not stop; `chimera service stop --now` stops it at once",
            record.pid
        ))
    }
}

/// Find, attach to, replace or start the backend — see the table at the top of this file.
pub fn ensure_backend(svc: &impl Services, wish: &Wish) -> Result<Ensured, String> {
    if wish.standalone {
        return spawn(svc, wish, true, None);
    }
    let mut record = svc.record();
    let mut probe = record.as_ref().map(|r| svc.probe(r));
    if probe == Some(Probe::Unreachable) {
        let deadline = Instant::now() + UNREACHABLE_PATIENCE;
        while probe == Some(Probe::Unreachable) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(500));
            record = svc.record();
            probe = record.as_ref().map(|r| svc.probe(r));
        }
    }
    match plan(record.as_ref(), probe.as_ref(), &wish.version, &wish.home) {
        Plan::Spawn => spawn(svc, wish, false, None),
        Plan::Attach => {
            let record = record.expect("an attach plan has a record");
            let token = record.token.clone();
            Ok(Ensured { handle: BackendHandle::Attached(attached_from(&record, false)), token, notice: None })
        }
        Plan::Replace => {
            let record = record.expect("a replace plan has a record");
            stop_service(svc, &record)?;
            spawn(svc, wish, false, Some(Notice::Replaced(record.version.clone())))
        }
        Plan::Standalone(other) => spawn(svc, wish, true, Some(Notice::OtherHome(other))),
        Plan::Refuse(why) => Err(why),
    }
}

fn spawn(svc: &impl Services, wish: &Wish, standalone: bool, notice: Option<Notice>) -> Result<Ensured, String> {
    if wish.service && !standalone {
        // The token the service enforces: the one it was given, or its own `service.token`, read
        // back from the record it just published (`spawn_service` waits for it).
        let (attached, token) = svc.spawn_service(wish.port, wish.token.as_deref())?;
        return Ok(Ensured { handle: BackendHandle::Attached(attached), token, notice });
    }
    let token = wish.token.clone().unwrap_or_else(|| wish.fresh_token.clone());
    let backend = svc.spawn_owned(wish.port, &token, standalone)?;
    Ok(Ensured { handle: BackendHandle::Owned(backend), token, notice })
}

pub fn attached_from(record: &Record, launched_here: bool) -> Attached {
    Attached {
        url: record.url.clone(),
        pid: record.pid,
        id: record.id.clone(),
        version: record.version.clone(),
        launched_here,
        log: PathBuf::new(),
    }
}

// ---- the operating system -------------------------------------------------------------------------

/// Whether `pid` runs. On Windows through `OpenProcess`/`GetExitCodeProcess` — never a signal,
/// which there would terminate it — declared by hand rather than through a new crate.
#[cfg(windows)]
pub fn pid_alive(pid: u32) -> bool {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut c_void;
        fn GetExitCodeProcess(handle: *mut c_void, code: *mut u32) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    if pid == 0 {
        return false;
    }
    // SAFETY: plain Win32 calls on a handle this function opens and closes; no memory is shared.
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code) != 0;
        CloseHandle(handle);
        !ok || code == STILL_ACTIVE
    }
}

#[cfg(not(windows))]
pub fn pid_alive(pid: u32) -> bool {
    pid != 0
        && Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
}

/// Ask the server who it is, and trust the record only when the answer matches it — the same
/// checks as the backend's `probe` (`chimera/core/service_record.py`).
pub fn probe_http(record: &Record) -> Probe {
    if !pid_alive(record.pid) {
        return Probe::Stale;
    }
    let reply = match sidecar_http::request(
        "GET", &record.url, "/api/info", Some(&record.token), &[], None, Duration::from_secs(3),
    ) {
        Ok(reply) => reply,
        Err(_) => return Probe::Unreachable,
    };
    match reply.status {
        401 => return Probe::Unauthorized,
        200 => {}
        other => return Probe::Incompatible(format!("/api/info answered {other}")),
    }
    let info = reply.json();
    let text = |key: &str| match info.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    };
    for (key, recorded) in [
        ("pid", record.pid.to_string()),
        ("version", record.version.clone()),
        ("id", record.id.clone()),
    ] {
        if text(key) != recorded {
            return Probe::Incompatible(format!("the record says {key}={recorded}, the server {}", text(key)));
        }
    }
    if normal_home(&text("home")) != normal_home(&record.home) {
        return Probe::Incompatible("the record and the server name different data folders".into());
    }
    Probe::Alive { protocol: info.get("protocol").and_then(Value::as_i64).unwrap_or(0) }
}

pub fn stop_http(record: &Record, drain: bool) -> Result<usize, String> {
    let reply = sidecar_http::request(
        "POST",
        &record.url,
        &format!("/api/service/stop?drain={}", u8::from(drain)),
        Some(&record.token),
        &[],
        None,
        Duration::from_secs(5),
    )?;
    if reply.status != 200 {
        return Err(format!("the background service refused to stop (HTTP {})", reply.status));
    }
    Ok(reply.json().get("draining").and_then(Value::as_u64).unwrap_or(0) as usize)
}

pub fn wait_gone(pid: u32, patience: Duration) -> bool {
    let deadline = Instant::now() + patience;
    while Instant::now() < deadline {
        if !pid_alive(pid) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    !pid_alive(pid)
}

/// What the service answered to a window's heartbeat.
#[derive(Debug, PartialEq)]
pub struct WindowAnswer {
    pub screen: bool,
    pub reason: Option<String>,
}

/// The window's heartbeat: "I am here", and — only to a service this shell started, with the key
/// it gave it — "hold this screen credential for me" (`chimera/api/service_windows.py`).
pub fn window_heartbeat(
    url: &str,
    token: &str,
    window: &str,
    lease: Option<(&str, &str)>,
) -> Result<WindowAnswer, String> {
    let (screen, headers): (Value, Vec<(&str, &str)>) = match lease {
        Some((screen, key)) => (json!(screen), vec![(LAUNCHER_HEADER, key)]),
        None => (Value::Null, Vec::new()),
    };
    let body = json!({ "window": window, "screen": screen });
    let reply = sidecar_http::request(
        "POST", url, "/api/service/window", Some(token), &headers, Some(&body), Duration::from_secs(3),
    )?;
    if reply.status != 200 {
        return Err(format!("HTTP {}", reply.status));
    }
    let answer = reply.json();
    Ok(WindowAnswer {
        screen: answer.get("screen").and_then(Value::as_bool).unwrap_or(false),
        reason: answer.get("reason").and_then(Value::as_str).map(str::to_string),
    })
}

/// The window is going: forget it now rather than when its lease runs out.
pub fn window_gone(url: &str, token: &str, window: &str) {
    let _ = sidecar_http::request(
        "DELETE",
        url,
        &format!("/api/service/window/{window}"),
        Some(token),
        &[],
        None,
        Duration::from_secs(1),
    );
}

/// Start `cmd` so it outlives this process: its own process group, no console, stdin closed, and
/// its output appended to `log` — never a pipe, which nobody reads once the app is gone (D10).
pub fn spawn_detached(cmd: &mut Command, log: &Path) -> Result<Child, String> {
    if let Some(parent) = log.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    rotate(log);
    let open = || {
        std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log)
            .map_err(|e| format!("cannot open {}: {e}", log.display()))
    };
    cmd.stdin(Stdio::null()).stdout(open()?).stderr(open()?);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;
        // Out of a kill-on-close job when the job allows it (a terminal, an IDE); otherwise without.
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB);
        if let Ok(child) = cmd.spawn() {
            return Ok(child);
        }
        cmd.creation_flags(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP);
        cmd.stdout(open()?).stderr(open()?);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn().map_err(|e| format!("failed to start the background service: {e}"))
}

/// Past this size the log is moved to `service.log.1` before the next start appends to it — the
/// backend's own `chimera service start` does the same (`LOG_ROTATE_BYTES`).
const LOG_ROTATE_BYTES: u64 = 5 * 1024 * 1024;

fn rotate(log: &Path) {
    if std::fs::metadata(log).is_ok_and(|m| m.len() > LOG_ROTATE_BYTES) {
        let mut old = log.as_os_str().to_owned();
        old.push(".1");
        let _ = std::fs::rename(log, PathBuf::from(old));
    }
}

/// The last `lines` lines of a log, for a report.
pub fn log_tail(log: &Path, lines: usize) -> String {
    let text = std::fs::read_to_string(log).unwrap_or_default();
    let all: Vec<&str> = text.lines().collect();
    all[all.len().saturating_sub(lines)..].join("\n")
}

// ---- PR 4: the service runs from its own copy ------------------------------------------------------

/// The executable the service is started from.
///
/// On Windows a running program's files cannot be overwritten, and the service now outlives the
/// app: run from the install folder, it would hold `sidecar-dist`'s DLLs open through the next
/// update, and the installer would meet the wall of "Error opening file for writing" that its hook
/// exists to prevent — or kill the owner's service mid-turn. So the service runs from a copy of
/// the frozen bundle in `<data_dir>/service-bin/<version>/`, as `chimera-service.exe`, made once per
/// version (~243 MB, ~4,000 files): the installer replaces the install folder freely, and its
/// `taskkill /IM chimera-backend.exe` never reaches the service. Elsewhere the bundled executable
/// is used as it is — nothing locks a file that is being replaced.
///
/// The copy is made into `<version>.partial-<pid>` and renamed into place only when complete, and
/// a `.complete` marker written last, so a copy interrupted by a crash is never run.
pub fn service_exe(bundled: &Path, data_dir: &Path, version: &str) -> Result<PathBuf, String> {
    if !cfg!(windows) {
        return Ok(bundled.to_path_buf());
    }
    copy_for_version(bundled, data_dir, version)
}

pub fn copy_for_version(bundled: &Path, data_dir: &Path, version: &str) -> Result<PathBuf, String> {
    let bin = data_dir.join(SERVICE_BIN);
    let target = bin.join(version);
    let exe = target.join(SERVICE_EXE);
    if target.join(COMPLETE).is_file() && exe.is_file() {
        return Ok(exe);
    }
    let source = bundled.parent().ok_or("the bundled backend has no folder")?;
    let partial = bin.join(format!("{version}.partial-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&partial);
    copy_tree(source, &partial).map_err(|e| format!("cannot copy the backend to {}: {e}", partial.display()))?;
    let bundled_name = bundled.file_name().ok_or("the bundled backend has no name")?;
    std::fs::rename(partial.join(bundled_name), partial.join(SERVICE_EXE))
        .map_err(|e| format!("cannot name the service executable: {e}"))?;
    std::fs::write(partial.join(COMPLETE), version).map_err(|e| e.to_string())?;
    if target.exists() {
        // An interrupted copy from an earlier launch (no marker): nothing runs from it.
        let _ = std::fs::remove_dir_all(&target);
    }
    std::fs::rename(&partial, &target).map_err(|e| format!("cannot put the service copy in place: {e}"))?;
    Ok(exe)
}

fn copy_tree(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(to)?;
    for entry in std::fs::read_dir(from)? {
        let entry = entry?;
        let dest = to.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &dest)?;
        } else {
            std::fs::copy(entry.path(), &dest)?;
        }
    }
    Ok(())
}

/// Remove the copies no process uses: every version but `keep`, and leftovers of interrupted copies.
///
/// "No process uses" is decided twice, and the second check is the one that matters. `in_use`
/// names the version a live service runs (its record). And a copy whose executable is running is
/// skipped because Windows refuses to open a running image for writing — a sharing violation that
/// no other program can cause on a file nothing has open. Renaming the folder is NOT that test:
/// measured on 2026-10-16, Windows renames a folder whose executable is running, and deleting it
/// afterwards would take every file the service has not locked — its web page, its data files —
/// from under it.
pub fn clean_old_copies(data_dir: &Path, keep: &str, in_use: Option<&str>) -> Vec<String> {
    let bin = data_dir.join(SERVICE_BIN);
    let mut removed = Vec::new();
    let Ok(entries) = std::fs::read_dir(&bin) else { return removed };
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let ours = format!(".partial-{}", std::process::id());
        if name == keep || Some(name.as_str()) == in_use || name.ends_with(&ours) || !entry.path().is_dir() {
            continue;
        }
        if runs_from(&entry.path()) {
            continue;
        }
        if std::fs::remove_dir_all(entry.path()).is_ok() {
            removed.push(name);
        }
    }
    removed
}

/// Whether a program in `dir` is running: one of its executables cannot be opened for writing.
fn runs_from(dir: &Path) -> bool {
    let Ok(entries) = std::fs::read_dir(dir) else { return false };
    entries.flatten().any(|entry| {
        let path = entry.path();
        let program = path.extension().is_some_and(|e| e.eq_ignore_ascii_case("exe"))
            || path.file_name().is_some_and(|n| n == SERVICE_EXE);
        program
            && path.is_file()
            && std::fs::OpenOptions::new().write(true).open(&path).is_err_and(|e| {
                // ERROR_SHARING_VIOLATION; or "access denied", which a running image also gives.
                e.raw_os_error() == Some(32) || e.kind() == std::io::ErrorKind::PermissionDenied
            })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    const OURS: &str = "0.67.4";

    fn home() -> PathBuf {
        std::env::temp_dir().join(format!("chimera-attach-home-{}", std::process::id()))
    }

    fn record(version: &str, home: &Path) -> Record {
        Record {
            id: "rec-1".into(),
            version: version.into(),
            protocol: PROTOCOL,
            url: "http://127.0.0.1:47615".into(),
            pid: 4242,
            token: "the-record-token".into(),
            home: home.to_string_lossy().to_string(),
            mode: "service".into(),
            started_at: 1.0,
        }
    }

    fn lingering() -> Child {
        Command::new(if cfg!(windows) { "cmd" } else { "sh" })
            .args(if cfg!(windows) { vec!["/C", "ping -n 20 127.0.0.1 >nul"] } else { vec!["-c", "sleep 20"] })
            .spawn()
            .expect("a lingering process")
    }

    /// The backend double: a scripted record and probe, and a log of everything done to the world.
    struct Double {
        records: RefCell<VecDeque<Option<Record>>>,
        probes: RefCell<VecDeque<Probe>>,
        done: RefCell<Vec<String>>,
        draining: usize,
        wait_for_turns: bool,
        gone: Cell<bool>,
    }

    impl Double {
        fn new(records: Vec<Option<Record>>, probes: Vec<Probe>) -> Self {
            Self {
                records: RefCell::new(records.into()),
                probes: RefCell::new(probes.into()),
                done: RefCell::new(Vec::new()),
                draining: 0,
                wait_for_turns: true,
                gone: Cell::new(true),
            }
        }
        fn did(&self) -> Vec<String> {
            self.done.borrow().clone()
        }
    }

    impl Services for Double {
        fn record(&self) -> Option<Record> {
            let mut r = self.records.borrow_mut();
            if r.len() > 1 { r.pop_front().flatten() } else { r.front().cloned().flatten() }
        }
        fn probe(&self, _record: &Record) -> Probe {
            let mut p = self.probes.borrow_mut();
            if p.len() > 1 { p.pop_front().unwrap() } else { p.front().cloned().unwrap_or(Probe::Stale) }
        }
        fn stop(&self, _record: &Record, drain: bool) -> Result<usize, String> {
            self.done.borrow_mut().push(format!("stop drain={drain}"));
            Ok(if drain { self.draining } else { 0 })
        }
        fn wait_gone(&self, _record: &Record, patience: Duration) -> bool {
            self.done.borrow_mut().push(format!("wait {}s", patience.as_secs()));
            self.gone.get()
        }
        fn spawn_service(&self, port: u16, token: Option<&str>) -> Result<(Attached, String), String> {
            self.done.borrow_mut().push(format!("spawn service :{port} token={token:?}"));
            let mut fresh = attached_from(&record(OURS, &home()), true);
            fresh.id = "rec-new".into();
            Ok((fresh, token.unwrap_or("the-new-record-token").to_string()))
        }
        fn spawn_owned(&self, port: u16, token: &str, standalone: bool) -> Result<Backend, String> {
            self.done.borrow_mut().push(format!("spawn owned :{port} token={token} standalone={standalone}"));
            Ok(Backend {
                child: lingering(),
                url: format!("http://127.0.0.1:{port}"),
                stderr: Arc::new(Mutex::new(VecDeque::new())),
            })
        }
        fn ask_drain(&self, running: usize) -> bool {
            self.done.borrow_mut().push(format!("ask {running}"));
            self.wait_for_turns
        }
    }

    fn wish(service: bool) -> Wish {
        let home = home();
        let _ = std::fs::create_dir_all(&home);
        Wish {
            service,
            standalone: false,
            port: 51234,
            version: OURS.into(),
            home,
            token: None,
            fresh_token: "fresh".into(),
        }
    }

    fn done_with(mut ensured: Ensured) -> Ensured {
        ensured.handle.kill_if_owned();
        ensured
    }

    #[test]
    fn a_valid_record_is_attached_to_without_starting_anything() {
        let svc = Double::new(vec![Some(record(OURS, &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        let ensured = ensure_backend(&svc, &wish(true)).expect("attached");
        let attached = ensured.handle.attached().expect("an attached handle").clone();
        assert_eq!(attached.url, "http://127.0.0.1:47615");
        assert!(!attached.launched_here, "this shell did not start it, so it cannot lease it a screen");
        assert_eq!(ensured.token, "the-record-token", "the window must send the token the service enforces");
        assert!(svc.did().is_empty(), "attaching started or stopped something: {:?}", svc.did());
    }

    #[test]
    fn a_newer_service_of_the_same_protocol_is_attached_to() {
        let svc = Double::new(vec![Some(record("0.68.0", &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        assert!(ensure_backend(&svc, &wish(true)).unwrap().handle.attached().is_some());
        assert!(svc.did().is_empty());
    }

    #[test]
    fn an_older_service_is_stopped_with_drain_and_replaced() {
        let svc = Double::new(
            vec![Some(record("0.66.0", &home())), Some(record(OURS, &home()))],
            vec![Probe::Alive { protocol: PROTOCOL }],
        );
        let ensured = ensure_backend(&svc, &wish(true)).expect("replaced");
        assert_eq!(ensured.notice, Some(Notice::Replaced("0.66.0".into())));
        let fresh = ensured.handle.attached().expect("the new service").clone();
        assert!(fresh.launched_here && fresh.id == "rec-new");
        assert_eq!(
            svc.did(),
            vec!["stop drain=true", "wait 60s", "spawn service :51234 token=None"],
            "the old one must be stopped (with drain) BEFORE the new one starts"
        );
    }

    #[test]
    fn turns_in_the_older_service_are_asked_about_and_waited_for() {
        let mut svc = Double::new(vec![Some(record("0.66.0", &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        svc.draining = 2;
        done_with(ensure_backend(&svc, &wish(true)).unwrap());
        assert_eq!(svc.did()[..3], ["stop drain=true", "ask 2", "wait 900s"]);
        // Told not to wait: stopped now.
        let mut now = Double::new(vec![Some(record("0.66.0", &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        now.draining = 2;
        now.wait_for_turns = false;
        done_with(ensure_backend(&now, &wish(true)).unwrap());
        assert_eq!(now.did()[..4], ["stop drain=true", "ask 2", "stop drain=false", "wait 60s"]);
    }

    #[test]
    fn an_older_service_that_will_not_stop_is_said_and_nothing_is_started() {
        let svc = Double::new(vec![Some(record("0.66.0", &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        svc.gone.set(false);
        let err = ensure_backend(&svc, &wish(true)).err().expect("an error");
        assert!(err.contains("did not stop"), "{err}");
        assert!(!svc.did().iter().any(|d| d.starts_with("spawn")), "{:?}", svc.did());
    }

    #[test]
    fn another_protocol_is_a_clear_error() {
        let svc = Double::new(vec![Some(record(OURS, &home()))], vec![Probe::Alive { protocol: PROTOCOL + 1 }]);
        let err = ensure_backend(&svc, &wish(true)).err().expect("refused");
        assert!(err.contains("protocol") && err.contains("chimera service stop"), "{err}");
        assert!(svc.did().is_empty());
    }

    #[test]
    fn a_service_of_another_data_folder_leaves_this_app_standalone() {
        let elsewhere = std::env::temp_dir().join("chimera-attach-somebody-else");
        let svc = Double::new(vec![Some(record(OURS, &elsewhere))], vec![Probe::Alive { protocol: PROTOCOL }]);
        let ensured = done_with(ensure_backend(&svc, &wish(true)).expect("standalone"));
        assert!(matches!(ensured.handle, BackendHandle::Owned(_)));
        assert!(matches!(ensured.notice, Some(Notice::OtherHome(_))));
        assert_eq!(svc.did(), vec!["spawn owned :51234 token=fresh standalone=true"]);
    }

    #[test]
    fn no_record_starts_the_service_or_an_owned_child_by_the_preference() {
        let svc = Double::new(vec![None], vec![]);
        let ensured = ensure_backend(&svc, &wish(true)).unwrap();
        assert!(ensured.handle.attached().is_some_and(|a| a.launched_here));
        assert_eq!(ensured.token, "the-new-record-token", "the token the new service published");
        let off = Double::new(vec![None], vec![]);
        let owned = done_with(ensure_backend(&off, &wish(false)).unwrap());
        assert!(matches!(owned.handle, BackendHandle::Owned(_)));
        assert_eq!(off.did(), vec!["spawn owned :51234 token=fresh standalone=false"]);
    }

    #[test]
    fn a_dead_record_is_litter_and_a_new_backend_starts() {
        let svc = Double::new(vec![Some(record(OURS, &home()))], vec![Probe::Stale]);
        assert!(ensure_backend(&svc, &wish(true)).unwrap().handle.attached().is_some_and(|a| a.launched_here));
        assert_eq!(svc.did(), vec!["spawn service :51234 token=None"]);
    }

    #[test]
    fn the_standalone_flag_ignores_the_record() {
        let svc = Double::new(vec![Some(record(OURS, &home()))], vec![Probe::Alive { protocol: PROTOCOL }]);
        let mut w = wish(true);
        w.standalone = true;
        w.token = Some("owners".into());
        let ensured = done_with(ensure_backend(&svc, &w).unwrap());
        assert!(matches!(ensured.handle, BackendHandle::Owned(_)) && ensured.token == "owners");
        assert_eq!(svc.did(), vec!["spawn owned :51234 token=owners standalone=true"]);
    }

    #[test]
    fn a_record_that_lies_is_refused_not_attached() {
        for probe in [Probe::Unauthorized, Probe::Incompatible("pid".into())] {
            let svc = Double::new(vec![Some(record(OURS, &home()))], vec![probe]);
            assert!(ensure_backend(&svc, &wish(true)).is_err());
            assert!(svc.did().is_empty());
        }
    }

    #[test]
    fn killing_never_reaches_an_attached_service() {
        let mut child = lingering();
        let pid = child.id();
        let mut handle = BackendHandle::Attached(Attached {
            url: "http://127.0.0.1:1".into(),
            pid,
            id: "x".into(),
            version: OURS.into(),
            launched_here: true,
            log: PathBuf::new(),
        });
        handle.kill_if_owned();
        std::thread::sleep(Duration::from_millis(300));
        assert!(child.try_wait().unwrap().is_none(), "the shell killed a service it does not own");
        assert!(pid_alive(pid));
        let _ = child.kill();
        let _ = child.wait();
        assert!(!pid_alive(pid), "a reaped process still reads as alive");
        let mut owned = BackendHandle::Owned(Backend {
            child: lingering(),
            url: String::new(),
            stderr: Arc::new(Mutex::new(VecDeque::new())),
        });
        owned.kill_if_owned();
        let backend = owned.owned_mut().unwrap();
        assert!(backend.child.wait().is_ok(), "the owned child was not killed");
    }

    #[test]
    fn versions_compare_by_number() {
        assert_eq!(compare_versions("0.67.4", "0.67.10"), Ordering::Less);
        assert_eq!(compare_versions("0.67.4", "0.67.4"), Ordering::Equal);
        assert_eq!(compare_versions("1.0", "0.99.9"), Ordering::Greater);
        assert_eq!(compare_versions("0.67.4+source", "0.67.4"), Ordering::Equal);
    }

    #[test]
    fn homes_compare_the_way_the_backend_writes_them() {
        assert_eq!(normal_home(r"\\?\C:\Users\X\data\"), normal_home(r"C:\Users\X\data"));
        if cfg!(windows) {
            assert_eq!(normal_home("C:/Users/X/Data"), normal_home(r"c:\users\x\data"));
        }
        let h = home();
        let _ = std::fs::create_dir_all(&h);
        let resolved = std::fs::canonicalize(&h).unwrap();
        assert!(same_home(&resolved.to_string_lossy(), &h));
        assert!(!same_home(&std::env::temp_dir().join("elsewhere").to_string_lossy(), &h));
    }

    #[test]
    fn a_record_without_a_token_is_no_record() {
        let dir = std::env::temp_dir().join(format!("chimera-attach-rec-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = record_path(&dir, "stable");
        std::fs::write(&path, r#"{"id":"a","version":"1","protocol":1,"url":"u","pid":1,"token":"","home":"h","mode":"service","started_at":0}"#).unwrap();
        assert!(read_record(&path).is_none());
        std::fs::write(&path, r#"{"id":"a","version":"1","protocol":1,"url":"u","pid":1,"token":"t","home":"h","mode":"service","started_at":0}"#).unwrap();
        assert_eq!(read_record(&path).map(|r| r.token), Some("t".into()));
    }

    #[test]
    fn the_copy_is_made_once_per_version_and_old_ones_go() {
        let root = std::env::temp_dir().join(format!("chimera-attach-copy-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bundle = root.join("bundle").join("chimera-backend");
        std::fs::create_dir_all(bundle.join("_internal")).unwrap();
        let bundled = bundle.join(if cfg!(windows) { "chimera-backend.exe" } else { "chimera-backend" });
        std::fs::write(&bundled, "exe").unwrap();
        std::fs::write(bundle.join("_internal").join("lib.dll"), "dll").unwrap();
        let data = root.join("data");
        let exe = copy_for_version(&bundled, &data, "0.67.4").expect("copied");
        assert_eq!(exe, data.join(SERVICE_BIN).join("0.67.4").join(SERVICE_EXE));
        assert!(data.join(SERVICE_BIN).join("0.67.4").join("_internal").join("lib.dll").is_file());
        // The same version again: no copy (a file changed in the bundle does not reach it).
        std::fs::write(&bundled, "exe2").unwrap();
        copy_for_version(&bundled, &data, "0.67.4").unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "exe");
        // A copy interrupted before its marker is made again.
        std::fs::remove_file(data.join(SERVICE_BIN).join("0.67.4").join(COMPLETE)).unwrap();
        copy_for_version(&bundled, &data, "0.67.4").unwrap();
        assert_eq!(std::fs::read_to_string(&exe).unwrap(), "exe2");
        // A newer version: copied beside; the old one goes, unless a live service runs it.
        copy_for_version(&bundled, &data, "0.68.0").unwrap();
        copy_for_version(&bundled, &data, "0.69.0").unwrap();
        let removed = clean_old_copies(&data, "0.69.0", Some("0.68.0"));
        assert_eq!(removed, vec!["0.67.4".to_string()]);
        assert!(data.join(SERVICE_BIN).join("0.68.0").is_dir(), "a copy in use was removed");
        let _ = std::fs::remove_dir_all(&root);
    }

    /// On Windows, a folder whose executable runs cannot be renamed, so it is never deleted from
    /// under the service that runs it.
    #[cfg(windows)]
    #[test]
    fn a_copy_a_process_runs_is_not_removed() {
        let root = std::env::temp_dir().join(format!("chimera-attach-busy-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let old = root.join(SERVICE_BIN).join("0.60.0");
        std::fs::create_dir_all(&old).unwrap();
        let exe = old.join(SERVICE_EXE);
        std::fs::copy(std::env::var("ComSpec").unwrap_or_else(|_| r"C:\Windows\System32\cmd.exe".into()), &exe).unwrap();
        let mut running = Command::new(&exe).args(["/C", "ping -n 20 127.0.0.1 >nul"]).spawn().unwrap();
        std::thread::sleep(Duration::from_millis(300));
        assert!(clean_old_copies(&root, "0.67.4", None).is_empty(), "a running copy was removed");
        assert!(exe.is_file());
        let _ = running.kill();
        let _ = running.wait();
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(clean_old_copies(&root, "0.67.4", None), vec!["0.60.0".to_string()]);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// The HTTP side against a real socket: the probe, the stop with drain, the heartbeat — what
    /// each sends, including that the launcher key goes ONLY with a lease.
    #[test]
    fn the_wire_carries_what_the_backend_reads() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let seen = Arc::new(Mutex::new(Vec::<String>::new()));
        let sink = Arc::clone(&seen);
        let me = std::process::id();
        let h = home();
        let _ = std::fs::create_dir_all(&h);
        let home_text = std::fs::canonicalize(&h).unwrap().to_string_lossy().to_string();
        let info = json!({"id": "rec-1", "version": OURS, "protocol": PROTOCOL, "pid": me, "home": home_text}).to_string();
        let server = std::thread::spawn(move || {
            for _ in 0..4 {
                let (mut s, _) = listener.accept().unwrap();
                let mut buf = vec![0u8; 8192];
                let n = s.read(&mut buf).unwrap();
                let req = String::from_utf8_lossy(&buf[..n]).to_string();
                let body = if req.starts_with("GET /api/info") {
                    info.clone()
                } else if req.starts_with("POST /api/service/stop") {
                    json!({"stopping": true, "draining": 3}).to_string()
                } else {
                    json!({"windows": 1, "screen": req.contains("X-Chimera-Launcher"), "reason": null, "lease_seconds": 30.0}).to_string()
                };
                sink.lock().unwrap().push(req);
                let _ = write!(s, "HTTP/1.0 200 OK\r\nContent-Length: {}\r\n\r\n{body}", body.len());
            }
        });
        let mut rec = record(OURS, &h);
        rec.url = url.clone();
        rec.pid = me;
        rec.home = home_text;
        assert_eq!(probe_http(&rec), Probe::Alive { protocol: PROTOCOL });
        assert_eq!(stop_http(&rec, true), Ok(3));
        let leased = window_heartbeat(&url, "tok", "main-1", Some(("screen-value", "launcher-key"))).unwrap();
        assert!(leased.screen);
        let present = window_heartbeat(&url, "tok", "main-1", None).unwrap();
        assert!(!present.screen);
        server.join().unwrap();
        let seen = seen.lock().unwrap();
        assert!(seen[0].contains("Authorization: Bearer the-record-token"));
        assert!(seen[1].starts_with("POST /api/service/stop?drain=1 "));
        assert!(seen[2].contains("X-Chimera-Launcher: launcher-key") && seen[2].contains("\"screen\":\"screen-value\""));
        assert!(!seen[3].contains("X-Chimera-Launcher") && seen[3].contains("\"screen\":null"));
    }
}
