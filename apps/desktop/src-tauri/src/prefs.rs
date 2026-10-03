//! The shell's own preferences: what the tray toggles, kept in a small JSON file the Rust reads.
//!
//! Why a file of its own and not the app's settings. The app's settings live behind the sidecar's
//! HTTP API and the page's `localStorage`, and neither is reachable from here without either
//! granting the window IPC (which `capabilities/default.json` deliberately does not) or making the
//! shell depend on a backend that may be the very thing that is down. These four switches decide
//! what the NATIVE process does — whether closing the window ends it, whether it flashes, which
//! chord it listens for — so they belong to the process that acts on them.
//!
//! Start-at-sign-in is not here on purpose: its truth is the operating system's (the `Run` key on
//! Windows, a LaunchAgent on macOS), and a second copy in this file could only ever disagree with it.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// The file, inside the app's data directory (the parent of the backend's `CHIMERA_HOME`).
pub const PREFS_FILE: &str = "shell-prefs.json";

/// The quick-entry chord when the file names none. `CommandOrControl` is Ctrl on Windows and Linux
/// and Cmd on macOS. Shift+Space rather than a bare Ctrl+Space, which is the input-method toggle
/// on many Windows and Linux keyboards and would be taken from the person the moment it was on.
pub const DEFAULT_CHORD: &str = "CommandOrControl+Shift+Space";

/// What the tray can switch.
///
/// `#[serde(default)]` on the struct, so a file written by an older shell — or edited by hand to
/// hold only the chord — fills every missing field with today's default instead of failing whole.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Prefs {
    /// Closing the window hides it and the backend keeps running (study 29, P2.2). OFF: it leaves an
    /// invisible process that can spend money, and that is a choice the owner makes, not a default.
    pub keep_in_tray: bool,
    /// Flash the taskbar when an approval is waiting and the window is not in front (P2.4). ON: it
    /// approves nothing and sends nothing anywhere; an unanswered approval refuses itself after
    /// `CHIMERA_APPROVAL_WAIT` (300 s), and today the only place it shows is inside the window.
    pub call_attention: bool,
    /// The system-wide quick-entry shortcut (P2.6). OFF: a global chord takes a key combination
    /// away from every other program on the machine.
    pub quick_entry: bool,
    /// The chord, in the accelerator syntax `global-hotkey` parses ("CommandOrControl+Shift+Space").
    /// Changed by editing this file; a chord that does not parse or is taken is reported in the tray.
    pub quick_entry_chord: String,
}

impl Default for Prefs {
    fn default() -> Self {
        Self {
            keep_in_tray: false,
            call_attention: true,
            quick_entry: false,
            quick_entry_chord: DEFAULT_CHORD.to_string(),
        }
    }
}

/// Where the file lives.
pub fn prefs_path(data_dir: &Path) -> PathBuf {
    data_dir.join(PREFS_FILE)
}

/// The preferences, and what went wrong reading them if anything did.
///
/// A missing file is the normal first run and is not an error. An unreadable or malformed one IS,
/// and it is returned rather than swallowed: the tray shows it, because a file someone edited by
/// hand and broke would otherwise just quietly stop meaning anything.
pub fn load(data_dir: &Path) -> (Prefs, Option<String>) {
    let path = prefs_path(data_dir);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (Prefs::default(), None),
        Err(e) => return (Prefs::default(), Some(e.to_string())),
    };
    match serde_json::from_str::<Prefs>(&text) {
        Ok(prefs) => (prefs, None),
        Err(e) => (Prefs::default(), Some(e.to_string())),
    }
}

/// Write the preferences, atomically: a crash mid-write must not leave half a file that then reads
/// as "malformed" and resets everything to defaults.
pub fn save(data_dir: &Path, prefs: &Prefs) -> Result<(), String> {
    let path = prefs_path(data_dir);
    let tmp = data_dir.join(format!("{PREFS_FILE}.tmp"));
    let body = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

/// What closing the main window does.
#[derive(Debug, PartialEq, Eq)]
pub enum OnClose {
    /// Hide it. The backend, its scheduled jobs, long runs and the app's bots keep going; the tray's
    /// "Quit" is what ends them.
    Hide,
    /// Let it close, which ends the app and — through the exit hook — the backend. What it has
    /// always done, and still the default.
    Close,
}

/// The whole rule, as a function the tests can call.
pub fn on_close(prefs: &Prefs) -> OnClose {
    if prefs.keep_in_tray {
        OnClose::Hide
    } else {
        OnClose::Close
    }
}

#[cfg(test)]
mod tests {
    use super::{load, on_close, prefs_path, save, OnClose, Prefs, DEFAULT_CHORD};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static N: AtomicUsize = AtomicUsize::new(0);

    fn dir(name: &str) -> PathBuf {
        let n = N.fetch_add(1, Ordering::SeqCst);
        let d = std::env::temp_dir().join(format!("chimera-prefs-{name}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// The defaults are the study's: tray and shortcut OFF, the flash ON.
    #[test]
    fn a_first_run_keeps_todays_behaviour_except_the_flash() {
        let d = dir("first");
        let (prefs, problem) = load(&d);
        assert_eq!(problem, None, "a missing file is a first run, not an error");
        assert!(!prefs.keep_in_tray, "closing the window must still end the app by default");
        assert!(!prefs.quick_entry, "a global chord must not be taken by default");
        assert!(prefs.call_attention);
        assert_eq!(prefs.quick_entry_chord, DEFAULT_CHORD);
        assert_eq!(on_close(&prefs), OnClose::Close);
    }

    #[test]
    fn closing_hides_only_when_the_owner_asked_for_the_tray() {
        let on = Prefs { keep_in_tray: true, ..Prefs::default() };
        assert_eq!(on_close(&on), OnClose::Hide);
        assert_eq!(on_close(&Prefs::default()), OnClose::Close);
    }

    #[test]
    fn what_is_saved_is_what_is_read_back() {
        let d = dir("round");
        let wanted = Prefs {
            keep_in_tray: true,
            call_attention: false,
            quick_entry: true,
            quick_entry_chord: "Alt+Shift+K".into(),
        };
        save(&d, &wanted).expect("saved");
        assert_eq!(load(&d), (wanted, None));
        assert!(!d.join("shell-prefs.json.tmp").exists(), "the temporary file was left behind");
    }

    /// A file edited by hand to hold only the chord keeps every other switch at its default.
    #[test]
    fn a_partial_file_fills_the_rest_with_defaults() {
        let d = dir("partial");
        std::fs::write(prefs_path(&d), r#"{"quick_entry_chord": "Alt+Q"}"#).unwrap();
        let (prefs, problem) = load(&d);
        assert_eq!(problem, None);
        assert_eq!(prefs.quick_entry_chord, "Alt+Q");
        assert!(prefs.call_attention && !prefs.keep_in_tray && !prefs.quick_entry);
    }

    /// A broken file is REPORTED, not silently treated as a first run.
    #[test]
    fn a_broken_file_is_reported_and_falls_back_to_defaults() {
        let d = dir("broken");
        std::fs::write(prefs_path(&d), "{ keep_in_tray: yes").unwrap();
        let (prefs, problem) = load(&d);
        assert_eq!(prefs, Prefs::default());
        assert!(problem.is_some(), "a malformed file read as a clean first run");
    }
}
