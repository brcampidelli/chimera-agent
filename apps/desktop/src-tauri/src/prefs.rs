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
//! What the file CAN carry is a request to change it (`start_at_sign_in`), which the shell carries
//! out and then removes.
//!
//! **The Settings screen writes this file too.** The page has no IPC, so it asks the backend, which
//! the shell started with this file's path in `CHIMERA_SHELL_PREFS` (`chimera/api/shell_prefs.py`).
//! The shell notices the change on its next tick (`adopt`) and acts on it, so a switch flipped on the
//! screen and one flipped in the tray end in the same place. What the shell can tell the screen back
//! — the operating system's answer for sign-in, and the tray's problem line — goes the other way, in
//! a second small file the backend only reads (`STATE_FILE`, path in `CHIMERA_SHELL_STATE`).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// The file, inside the app's data directory (the parent of the backend's `CHIMERA_HOME`).
pub const PREFS_FILE: &str = "shell-prefs.json";

/// What the shell reports back to the Settings screen, beside the preferences. Written by the shell
/// only; the backend reads it and never writes it.
pub const STATE_FILE: &str = "shell-state.json";

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
    /// A REQUEST to turn start-at-sign-in on or off, written by the Settings screen. Not the state:
    /// that is the operating system's, and the shell reports it in `STATE_FILE`. The shell applies
    /// the request on its next look at this file and writes the file back without it, so the field
    /// is absent from every file the shell itself saved.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub start_at_sign_in: Option<bool>,
}

impl Default for Prefs {
    fn default() -> Self {
        Self {
            keep_in_tray: false,
            call_attention: true,
            quick_entry: false,
            quick_entry_chord: DEFAULT_CHORD.to_string(),
            start_at_sign_in: None,
        }
    }
}

/// Where the file lives.
pub fn prefs_path(data_dir: &Path) -> PathBuf {
    data_dir.join(PREFS_FILE)
}

/// Where the shell's report to the Settings screen lives.
pub fn state_path(data_dir: &Path) -> PathBuf {
    data_dir.join(STATE_FILE)
}

/// When the preferences file last changed, or `None` when there is none. The watcher compares this
/// on every tick, so a file nobody touched costs one `stat`, not a parse.
pub fn modified(data_dir: &Path) -> Option<std::time::SystemTime> {
    std::fs::metadata(prefs_path(data_dir)).and_then(|m| m.modified()).ok()
}

/// What taking the file's contents in has to be followed by.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Adopted {
    /// Anything differed: the tray's check items have to be set again.
    pub changed: bool,
    /// The shortcut switch or its chord differed: the chord has to be registered again.
    pub quick_changed: bool,
    /// A sign-in request to carry out (and then remove from the file).
    pub sign_in: Option<bool>,
}

/// Take what the file says as the preferences in force, and say what that obliges the shell to do.
///
/// Pure, so the rule is tested without an app. The request is taken OUT of what is kept: the
/// preferences in memory never carry one, so the next save writes the file without it — which is
/// how the request is consumed rather than carried out again at every tick.
pub fn adopt(memory: &mut Prefs, mut disk: Prefs) -> Adopted {
    let sign_in = disk.start_at_sign_in.take();
    let quick_changed =
        memory.quick_entry != disk.quick_entry || memory.quick_entry_chord != disk.quick_entry_chord;
    let changed = *memory != disk;
    *memory = disk;
    Adopted { changed, quick_changed, sign_in }
}

/// The report the Settings screen reads, as the file's body. `start_at_sign_in` is the operating
/// system's answer (`None` when it could not be read); `problem` is the tray's own line, so the
/// screen and the tray never disagree about what is wrong.
pub fn state_body(start_at_sign_in: Option<bool>, problem: Option<&str>) -> String {
    serde_json::json!({
        "start_at_sign_in": start_at_sign_in,
        "problem": problem.unwrap_or(""),
    })
    .to_string()
}

/// Write the report, atomically, for the reason `save` is atomic: half a file reads as none.
pub fn save_state(
    data_dir: &Path,
    start_at_sign_in: Option<bool>,
    problem: Option<&str>,
) -> Result<(), String> {
    let path = state_path(data_dir);
    let tmp = data_dir.join(format!("{STATE_FILE}.tmp"));
    std::fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, state_body(start_at_sign_in, problem)).map_err(|e| e.to_string())?;
    retry_denied(|| std::fs::rename(&tmp, &path)).map_err(|e| e.to_string())
}

/// How many times a replace that Windows refused is tried again, and the pause before each try.
///
/// The backend reads these files (the Settings card asks every few seconds, every two while a
/// request is out), and Python opens a file without `FILE_SHARE_DELETE`: a rename over it during
/// that read fails with `ERROR_ACCESS_DENIED` (os error 5). The read lasts microseconds, so a short
/// wait clears it; eight tries spread over about half a second is far past any read and still short
/// enough to sit on the main thread.
const DENIED_TRIES: u32 = 8;
const DENIED_PAUSE: std::time::Duration = std::time::Duration::from_millis(15);

/// Run `op` again while it fails with "access denied", up to `DENIED_TRIES` times. Any other error
/// is returned at once: a missing directory does not get better by waiting.
pub fn retry_denied(mut op: impl FnMut() -> std::io::Result<()>) -> std::io::Result<()> {
    let mut tries = 0;
    loop {
        match op() {
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied && tries + 1 < DENIED_TRIES => {
                tries += 1;
                std::thread::sleep(DENIED_PAUSE * tries);
            }
            other => return other,
        }
    }
}

/// Take a pending sign-in request out of the file, leaving every other key exactly as it is on disk.
///
/// Two callers. The shell, after carrying a request out: it re-reads the file rather than saving
/// its memory, because the Settings screen may have changed another switch since the look that
/// found the request, and a whole-struct save would put the old value back. And the tray's own
/// sign-in item: a click there is the owner's latest word, and a request left in the file would be
/// carried out at the next look and undo it.
///
/// `Ok(true)` when a request was removed, `Ok(false)` when there was none (the file is not touched).
/// A file that does not parse is an error and is left alone: it is someone's hand edit.
pub fn remove_request(data_dir: &Path) -> Result<bool, String> {
    let path = prefs_path(data_dir);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(e) => return Err(e.to_string()),
    };
    let mut value: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    let Some(object) = value.as_object_mut() else {
        return Err("the preferences file is not a JSON object".to_string());
    };
    if object.remove("start_at_sign_in").is_none() {
        return Ok(false);
    }
    let body = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    let tmp = data_dir.join(format!("{PREFS_FILE}.tmp"));
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    retry_denied(|| std::fs::rename(&tmp, &path)).map_err(|e| e.to_string())?;
    Ok(true)
}

/// After a sign-in request was carried out: remove it from the file, and say what the watcher's
/// "last seen" stamp becomes.
///
/// The stamp moves on ONLY when the request is gone. When the removal fails — a rename Windows kept
/// refusing, a hand edit that broke the file — the stamp stays where it was before the look, so the
/// next tick reads the file again, carries the request out again (asking the OS for what it already
/// has is harmless) and retries the removal. Advancing it anyway would leave the request in the file
/// with nothing looking at it, to be carried out again at the next launch, after the owner may have
/// changed their mind from the tray.
pub fn finish_request(
    data_dir: &Path,
    seen_before: Option<std::time::SystemTime>,
) -> (Option<std::time::SystemTime>, Result<bool, String>) {
    let removed = remove_request(data_dir);
    let seen = if removed.is_ok() { modified(data_dir) } else { seen_before };
    (seen, removed)
}

/// The file's text as preferences, or why it is not.
///
/// An object only. serde's derived `Deserialize` also accepts a JSON ARRAY for a struct, read by
/// position (`[true]` would turn the tray on) — found when the backend's reader was pinned to this
/// one by shared cases. Nobody writes this file as an array, and a second reader cannot be expected
/// to reproduce positional field order, so an array is refused here like any other shape that is
/// not the file's.
pub fn parse(text: &str) -> Result<Prefs, String> {
    let value: serde_json::Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    if !value.is_object() {
        return Err("the preferences file is not a JSON object".to_string());
    }
    serde_json::from_str::<Prefs>(text).map_err(|e| e.to_string())
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
    match parse(&text) {
        Ok(prefs) => (prefs, None),
        Err(e) => (Prefs::default(), Some(e.to_string())),
    }
}

/// Write the preferences, atomically: a crash mid-write must not leave half a file that then reads
/// as "malformed" and resets everything to defaults.
///
/// Returns where a broken file was moved, if one was. A file that does not parse is someone's hand
/// edit — a chord with a typo, a missing comma — and the switches in memory are the defaults that
/// stood in for it. Writing them over it would throw that edit away without a word on the first
/// click of any switch. So it is moved aside first (`shell-prefs.json.bad`, or a dated name when
/// that is taken), and if it cannot be moved nothing is written: the edit outranks the click.
pub fn save(data_dir: &Path, prefs: &Prefs) -> Result<Option<PathBuf>, String> {
    let path = prefs_path(data_dir);
    let tmp = data_dir.join(format!("{PREFS_FILE}.tmp"));
    let body = serde_json::to_string_pretty(prefs).map_err(|e| e.to_string())?;
    std::fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    let set_aside = set_aside_if_broken(data_dir, &path)?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    retry_denied(|| std::fs::rename(&tmp, &path)).map_err(|e| e.to_string())?;
    Ok(set_aside)
}

/// Move the file out of the way when it exists and does not parse; say where it went.
fn set_aside_if_broken(data_dir: &Path, path: &Path) -> Result<Option<PathBuf>, String> {
    let broken = match std::fs::read_to_string(path) {
        Ok(text) => parse(&text).is_err(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        // Unreadable (a lock, a permission): not provably ours to replace, so not replaced.
        Err(e) => return Err(e.to_string()),
    };
    if !broken {
        return Ok(None);
    }
    let mut aside = data_dir.join(format!("{PREFS_FILE}.bad"));
    if aside.exists() {
        // An earlier broken edit is already there; keep it too.
        let secs = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_secs());
        aside = data_dir.join(format!("{PREFS_FILE}.bad-{secs}"));
    }
    std::fs::rename(path, &aside).map_err(|e| e.to_string())?;
    Ok(Some(aside))
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
    use super::{
        adopt, finish_request, load, modified, on_close, prefs_path, remove_request, retry_denied,
        save, save_state, state_path, Adopted, OnClose, Prefs, DEFAULT_CHORD,
    };
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
            start_at_sign_in: None,
        };
        assert_eq!(save(&d, &wanted), Ok(None), "a missing file has nothing to set aside");
        assert_eq!(load(&d), (wanted.clone(), None));
        assert_eq!(save(&d, &wanted), Ok(None), "a good file is replaced, not set aside");
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

    /// The first switch clicked after a broken hand edit does not erase the edit: the file is moved
    /// aside with its bytes intact, and a second broken edit does not overwrite the first one.
    #[test]
    fn a_broken_file_is_set_aside_before_the_first_save() {
        let d = dir("aside");
        let edit = "{ \"quick_entry_chord\": \"Ctrl+Alt+K\", }";
        std::fs::write(prefs_path(&d), edit).unwrap();
        let on = Prefs { keep_in_tray: true, ..Prefs::default() };
        let aside = save(&d, &on).expect("saved").expect("the broken file was moved somewhere");
        assert_eq!(aside, d.join("shell-prefs.json.bad"));
        assert_eq!(std::fs::read_to_string(&aside).unwrap(), edit, "the hand edit was lost");
        assert_eq!(load(&d), (on.clone(), None));

        std::fs::write(prefs_path(&d), "not json either").unwrap();
        let second = save(&d, &on).expect("saved").expect("set aside again");
        assert_ne!(second, aside, "the second broken file overwrote the first");
        assert_eq!(std::fs::read_to_string(&aside).unwrap(), edit);
        assert_eq!(std::fs::read_to_string(&second).unwrap(), "not json either");
    }
    /// The Settings screen writes the file the tray writes. A switch it flipped is taken in, and
    /// the shell is told what that obliges: set the check items again, and register the chord again
    /// only when the shortcut changed.
    #[test]
    fn a_switch_flipped_on_the_settings_screen_is_taken_in_on_the_next_look() {
        let d = dir("adopt");
        std::fs::write(prefs_path(&d), r#"{"keep_in_tray": true, "call_attention": true}"#).unwrap();
        let mut memory = Prefs::default();
        let (disk, problem) = load(&d);
        assert_eq!(problem, None);
        let done = adopt(&mut memory, disk);
        assert!(memory.keep_in_tray, "the screen's switch did not reach the preferences in force");
        assert_eq!(done, Adopted { changed: true, quick_changed: false, sign_in: None });

        let again = load(&d).0;
        assert_eq!(adopt(&mut memory, again), Adopted::default(), "an unchanged file is not a change");

        let quick = Prefs { quick_entry: true, ..memory.clone() };
        assert!(adopt(&mut memory, quick).quick_changed, "the chord has to be registered again");
    }

    /// The sign-in request is carried out once: it is handed to the shell and kept out of the
    /// preferences in force, so the next save writes the file without it.
    #[test]
    fn a_sign_in_request_is_handed_over_once_and_never_saved_back() {
        let d = dir("signin");
        std::fs::write(prefs_path(&d), r#"{"start_at_sign_in": true}"#).unwrap();
        let mut memory = Prefs::default();
        let done = adopt(&mut memory, load(&d).0);
        assert_eq!(done.sign_in, Some(true));
        assert_eq!(memory.start_at_sign_in, None, "the request stayed in force and would be redone");
        save(&d, &memory).unwrap();
        let body = std::fs::read_to_string(prefs_path(&d)).unwrap();
        assert!(!body.contains("start_at_sign_in"), "the consumed request was written back: {body}");
        assert_eq!(adopt(&mut memory, load(&d).0).sign_in, None);
    }

    /// What the screen reads back: the OS's answer and the tray's line, never a guess. An answer
    /// the OS did not give is written as null, not as "off".
    #[test]
    fn the_report_carries_the_os_answer_and_the_tray_line() {
        let d = dir("state");
        save_state(&d, Some(true), Some("Ctrl+Shift+Space is taken")).unwrap();
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(state_path(&d)).unwrap()).unwrap();
        assert_eq!(v["start_at_sign_in"], serde_json::Value::Bool(true));
        assert_eq!(v["problem"], "Ctrl+Shift+Space is taken");
        save_state(&d, None, None).unwrap();
        let v: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(state_path(&d)).unwrap()).unwrap();
        assert!(v["start_at_sign_in"].is_null(), "an unread answer was reported as an answer");
        assert_eq!(v["problem"], "");
    }
    /// Windows refuses a rename over a file another process is reading; the replace waits and tries
    /// again, and gives up only after its tries, with the refusal. Other errors are not retried.
    #[test]
    fn a_refused_replace_is_tried_again_and_other_errors_are_not() {
        use std::io::{Error, ErrorKind};
        let mut calls = 0;
        let ok = retry_denied(|| {
            calls += 1;
            if calls < 3 { Err(Error::from(ErrorKind::PermissionDenied)) } else { Ok(()) }
        });
        assert!(ok.is_ok() && calls == 3, "a refusal that cleared was not waited out ({calls} calls)");

        let mut calls = 0;
        let stuck = retry_denied(|| {
            calls += 1;
            Err(Error::from(ErrorKind::PermissionDenied))
        });
        assert_eq!(stuck.unwrap_err().kind(), ErrorKind::PermissionDenied);
        assert_eq!(calls, 8, "a refusal that never clears must still end");

        let mut calls = 0;
        let missing = retry_denied(|| {
            calls += 1;
            Err(Error::from(ErrorKind::NotFound))
        });
        assert!(missing.is_err() && calls == 1, "a missing file was waited on");
    }

    /// The request is removed from what is on disk NOW, not from the shell's memory: a switch the
    /// screen saved after the look that found the request survives, and so does a key this shell
    /// does not know.
    #[test]
    fn carrying_out_a_request_removes_that_key_and_nothing_else() {
        let d = dir("consume");
        std::fs::write(prefs_path(&d), r#"{"start_at_sign_in": true}"#).unwrap();
        let mut memory = Prefs::default();
        assert_eq!(adopt(&mut memory, load(&d).0).sign_in, Some(true));
        // Between the look and the removal, the screen turns the tray on.
        std::fs::write(
            prefs_path(&d),
            r#"{"start_at_sign_in": true, "keep_in_tray": true, "from_a_newer_shell": 7}"#,
        )
        .unwrap();

        let (seen, removed) = finish_request(&d, None);
        assert_eq!(removed, Ok(true));
        assert_eq!(seen, modified(&d), "the stamp did not move on to the rewritten file");
        let on_disk: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(prefs_path(&d)).unwrap()).unwrap();
        assert_eq!(on_disk, serde_json::json!({"keep_in_tray": true, "from_a_newer_shell": 7}));
    }

    /// A removal that fails leaves the stamp where it was, so the next look tries again — instead
    /// of leaving the request in the file for the next launch to carry out.
    #[test]
    fn a_request_that_could_not_be_removed_is_looked_at_again() {
        let d = dir("stuck");
        let before = Some(std::time::SystemTime::UNIX_EPOCH);
        std::fs::write(prefs_path(&d), r#"{ "start_at_sign_in": true, "#).unwrap();

        let (seen, removed) = finish_request(&d, before);
        assert!(removed.is_err(), "a file that does not parse was rewritten");
        assert_eq!(seen, before, "the stamp moved on and the request will not be retried");
        assert_eq!(std::fs::read_to_string(prefs_path(&d)).unwrap(), r#"{ "start_at_sign_in": true, "#);
    }

    /// A click on the tray's own sign-in item clears a request the screen left pending; with none
    /// pending, the file is not rewritten at all.
    #[test]
    fn a_tray_click_clears_a_pending_request_and_touches_nothing_otherwise() {
        let d = dir("trayclick");
        assert_eq!(remove_request(&d), Ok(false), "no file is no request");
        std::fs::write(prefs_path(&d), r#"{"quick_entry_chord": "Alt+Q", "start_at_sign_in": false}"#).unwrap();
        assert_eq!(remove_request(&d), Ok(true));
        assert_eq!(load(&d).0.start_at_sign_in, None);
        assert_eq!(load(&d).0.quick_entry_chord, "Alt+Q");

        let body = std::fs::read_to_string(prefs_path(&d)).unwrap();
        assert_eq!(remove_request(&d), Ok(false));
        assert_eq!(std::fs::read_to_string(prefs_path(&d)).unwrap(), body, "a file with no request was rewritten");
    }

    /// The backend reads this file too (`chimera/api/shell_prefs.py`), and must call "readable" exactly
    /// what serde calls readable: a file the shell rejects whole is on its defaults, and a screen that
    /// showed it as set would describe settings nobody is using. Both sides read the SAME cases.
    #[test]
    fn the_shared_cases_get_the_verdicts_the_backend_gives_them() {
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../../../../tests/fixtures/shell_prefs_cases.json")).unwrap();
        let cases = cases.as_array().expect("a list of cases");
        assert!(cases.len() >= 10, "the shared cases went missing");
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let body = case["body"].as_str().unwrap();
            let parsed = super::parse(body);
            assert_eq!(parsed.is_ok(), case["valid"].as_bool().unwrap(), "{name}: {parsed:?}");
            if let (Ok(prefs), Some(expect)) = (parsed, case.get("expect")) {
                assert_eq!(serde_json::json!(prefs.keep_in_tray), expect["keep_in_tray"], "{name}");
                assert_eq!(serde_json::json!(prefs.call_attention), expect["call_attention"], "{name}");
                assert_eq!(serde_json::json!(prefs.quick_entry), expect["quick_entry"], "{name}");
                assert_eq!(serde_json::json!(prefs.quick_entry_chord), expect["quick_entry_chord"], "{name}");
                assert_eq!(serde_json::json!(prefs.start_at_sign_in), expect["start_at_sign_in"], "{name}");
            }
        }
    }
}
