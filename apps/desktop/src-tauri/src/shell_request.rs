//! The four troubleshooting actions the Settings screen may ask of the shell (C7).
//!
//! Open the logs folder, open the data folder, restart the backend, move the data folder to a dated
//! backup. The window cannot call the shell — it loads the sidecar's http origin with no IPC granted
//! (`capabilities/default.json`), and granting it would hand native calls to any script the page
//! ever runs. So the screen asks the backend, and the backend leaves a request file here
//! (`chimera/api/shell_requests.py`), the way the start-at-sign-in request already travels.
//!
//! The rule this module exists to keep: **the request is a WORD, and every path is ours.** The file
//! carries one of four action names and nothing else; [`Request::parse`] accepts exactly those, and
//! [`folder_path`] maps a folder to a path built from the data directory this process resolved at
//! launch. A request that tries to name a path — from the page, from a tool call, from a document
//! the agent read — is not a request at all.
//!
//! Std only, so `rustc --test` runs these tests without building the app.

use std::path::{Path, PathBuf};

/// The file the backend leaves a request in, beside the shell's preferences.
pub const REQUEST_FILE: &str = "shell-request.txt";
/// The shell's account of the last reset, which the backend reads back for the screen.
pub const RESULT_FILE: &str = "shell-request-result.txt";
/// The backend's home inside the data directory — `CHIMERA_HOME` in `start_sidecar`.
pub const BACKEND_HOME: &str = "data";
/// Files a reset carries back into the fresh home instead of leaving them only in the backup:
/// credentials are never reset as a side effect of "reset the app's data" (the owner removes them
/// on purpose, separately), and the installation id is the label support already has.
pub const CARRIED: [&str; 2] = [".env", ".installation-id"];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Folder {
    Logs,
    Data,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Request {
    Open(Folder),
    Restart,
    ResetData,
}

impl Request {
    /// The whole file, trimmed, must be exactly one of the four words. Anything else — a path, a
    /// second line, a word with arguments — is refused, never partly read.
    pub fn parse(text: &str) -> Option<Request> {
        match text.trim() {
            "open_logs" => Some(Request::Open(Folder::Logs)),
            "open_data" => Some(Request::Open(Folder::Data)),
            "restart" => Some(Request::Restart),
            "reset_data" => Some(Request::ResetData),
            _ => None,
        }
    }
}

pub fn request_path(data_dir: &Path) -> PathBuf {
    data_dir.join(REQUEST_FILE)
}

pub fn result_path(data_dir: &Path) -> PathBuf {
    data_dir.join(RESULT_FILE)
}

/// Where a folder is. Fixed by this process: the backend's log (`<home>/logs/backend.log`,
/// `chimera/cli/commands/serve.py`) and the data directory the crash reports are written to.
pub fn folder_path(data_dir: &Path, folder: Folder) -> PathBuf {
    match folder {
        Folder::Logs => data_dir.join(BACKEND_HOME).join("logs"),
        Folder::Data => data_dir.to_path_buf(),
    }
}

/// Take the pending request out of the file: `None` when there is none.
///
/// The file is removed BEFORE the request is carried out, and a request whose file cannot be
/// removed is not carried out at all. The order matters for exactly one action: a reset left in the
/// file would run again at the next tick and move the FRESH data folder aside too.
pub fn take(data_dir: &Path) -> Option<Result<Request, String>> {
    let path = request_path(data_dir);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return None,
        Err(e) => return Some(Err(format!("cannot read {}: {e}", path.display()))),
    };
    if let Err(e) = std::fs::remove_file(&path) {
        return Some(Err(format!("cannot remove {}: {e}", path.display())));
    }
    Some(Request::parse(&text).ok_or_else(|| "not a known request".to_string()))
}

/// `data-backup-YYYYMMDD-HHMMSS`, in UTC, from Unix seconds.
pub fn backup_name(unix: u64) -> String {
    let days = (unix / 86_400) as i64;
    let secs = unix % 86_400;
    // Howard Hinnant's days-to-civil, for a date without a date crate.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!(
        "data-backup-{year:04}{month:02}{day:02}-{:02}{:02}{:02}",
        secs / 3600,
        (secs % 3600) / 60,
        secs % 60
    )
}

/// Move the backend's home to a dated backup beside it. Never deletes: the call is a rename, and a
/// name already taken gets a suffix rather than being replaced.
///
/// `Ok(None)` when there is no home to move. The [`CARRIED`] files are copied back into a fresh
/// home; a copy that fails leaves the file in the backup, where it still is.
pub fn move_to_backup(data_dir: &Path, unix: u64) -> Result<Option<PathBuf>, String> {
    let home = data_dir.join(BACKEND_HOME);
    if std::fs::symlink_metadata(&home).is_err() {
        return Ok(None);
    }
    let base = backup_name(unix);
    let mut backup = data_dir.join(&base);
    let mut n = 2;
    while std::fs::symlink_metadata(&backup).is_ok() {
        backup = data_dir.join(format!("{base}-{n}"));
        n += 1;
    }
    std::fs::rename(&home, &backup)
        .map_err(|e| format!("could not move {} to {}: {e}", home.display(), backup.display()))?;
    for name in CARRIED {
        let kept = backup.join(name);
        if kept.is_file() && std::fs::create_dir_all(&home).is_ok() {
            let _ = std::fs::copy(&kept, home.join(name));
        }
    }
    Ok(Some(backup))
}

/// The two lines `chimera/api/shell_requests.py::last_reset` reads.
pub fn result_body(outcome: &Result<Option<PathBuf>, String>, unix: u64) -> String {
    let first = match outcome {
        Ok(Some(backup)) => format!("moved {}", backup.display()),
        Ok(None) => "failed there was no data folder to move".to_string(),
        Err(why) => format!("failed {}", why.replace(['\r', '\n'], " ")),
    };
    format!("{first}\n{unix}\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn scratch() -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "chimera-shell-request-{}-{}",
            std::process::id(),
            N.fetch_add(1, Ordering::SeqCst)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn only_the_four_words_are_requests() {
        assert_eq!(Request::parse("open_logs\n"), Some(Request::Open(Folder::Logs)));
        assert_eq!(Request::parse("open_data"), Some(Request::Open(Folder::Data)));
        assert_eq!(Request::parse("restart"), Some(Request::Restart));
        assert_eq!(Request::parse(" reset_data \n"), Some(Request::ResetData));
        for refused in [
            "",
            "open",
            "OPEN_LOGS",
            "open_logs C:\\Windows",
            "open_logs\n/etc",
            "C:\\Users\\someone",
            "/home/someone",
            "{\"action\":\"open_logs\",\"path\":\"/\"}",
            "open_folder",
        ] {
            assert_eq!(Request::parse(refused), None, "{refused:?} was taken as a request");
        }
    }

    #[test]
    fn every_folder_is_a_fixed_path_under_the_data_directory() {
        let data_dir = Path::new("/fixed/app-data");
        assert_eq!(folder_path(data_dir, Folder::Logs), data_dir.join("data").join("logs"));
        assert_eq!(folder_path(data_dir, Folder::Data), data_dir.to_path_buf());
        for folder in [Folder::Logs, Folder::Data] {
            assert!(folder_path(data_dir, folder).starts_with(data_dir));
        }
    }

    #[test]
    fn a_request_is_taken_once_and_a_garbled_one_is_refused() {
        let dir = scratch();
        assert!(take(&dir).is_none(), "no file is no request");
        std::fs::write(request_path(&dir), "reset_data\n").unwrap();
        assert_eq!(take(&dir), Some(Ok(Request::ResetData)));
        assert!(!request_path(&dir).exists(), "the request must leave the file before it runs");
        assert!(take(&dir).is_none(), "a reset must not run twice");
        std::fs::write(request_path(&dir), "open_logs ../../..\n").unwrap();
        assert!(matches!(take(&dir), Some(Err(_))));
        assert!(!request_path(&dir).exists(), "a refused request is still removed");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_backup_is_named_by_its_utc_date() {
        assert_eq!(backup_name(0), "data-backup-19700101-000000");
        // 2026-10-16T13:45:07Z
        assert_eq!(backup_name(1_792_158_307), "data-backup-20261016-134507");
        // A leap day.
        assert_eq!(backup_name(951_782_400), "data-backup-20000229-000000");
    }

    #[test]
    fn a_reset_moves_the_home_aside_and_deletes_nothing() {
        let dir = scratch();
        let home = dir.join("data");
        std::fs::create_dir_all(home.join("sessions")).unwrap();
        std::fs::write(home.join("sessions").join("s.json"), "{\"conversation\":1}").unwrap();
        std::fs::write(home.join("memory.json"), "[]").unwrap();
        std::fs::write(home.join(".env"), "OPENROUTER_API_KEY=k\n").unwrap();
        std::fs::write(home.join(".installation-id"), "abc\n").unwrap();
        std::fs::write(dir.join("backend-crash.txt"), "kept").unwrap();

        let backup = move_to_backup(&dir, 0).unwrap().expect("a home was there to move");

        assert_eq!(backup, dir.join("data-backup-19700101-000000"));
        assert_eq!(std::fs::read_to_string(backup.join("sessions").join("s.json")).unwrap(), "{\"conversation\":1}");
        assert_eq!(std::fs::read_to_string(backup.join("memory.json")).unwrap(), "[]");
        assert!(!home.join("sessions").exists() && !home.join("memory.json").exists(), "the home starts fresh");
        // Credentials and the id come back; the backup keeps its copy too.
        assert_eq!(std::fs::read_to_string(home.join(".env")).unwrap(), "OPENROUTER_API_KEY=k\n");
        assert!(backup.join(".env").exists());
        assert_eq!(std::fs::read_to_string(home.join(".installation-id")).unwrap(), "abc\n");
        assert!(dir.join("backend-crash.txt").exists(), "only the backend's home moves");

        // A second reset in the same second never overwrites the first backup.
        std::fs::write(home.join("memory.json"), "[2]").unwrap();
        let second = move_to_backup(&dir, 0).unwrap().unwrap();
        assert_eq!(second, dir.join("data-backup-19700101-000000-2"));
        assert_eq!(std::fs::read_to_string(backup.join("memory.json")).unwrap(), "[]");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_home_is_nothing_to_move_and_the_result_says_so() {
        let dir = scratch();
        let outcome = move_to_backup(&dir, 5);
        assert_eq!(outcome, Ok(None));
        assert_eq!(result_body(&outcome, 5), "failed there was no data folder to move\n5\n");
        let moved: Result<Option<PathBuf>, String> = Ok(Some(PathBuf::from("/x/data-backup-1")));
        assert_eq!(result_body(&moved, 7), "moved /x/data-backup-1\n7\n");
        let failed: Result<Option<PathBuf>, String> = Err("denied\nby the OS".into());
        assert_eq!(result_body(&failed, 9), "failed denied by the OS\n9\n");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
