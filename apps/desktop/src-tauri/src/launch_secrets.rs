//! How the shell hands the backend its secrets: one JSON line on the child's stdin, then closed.
//!
//! The per-launch screen credential, the per-launch bearer token and the service's launcher key used
//! to travel in the child's ENVIRONMENT. The backend removed them from its own `os.environ` as it
//! read them, but that is the wrong end of the problem: on Linux `/proc/<pid>/environ` keeps showing
//! the environment the process was *started* with, to every process of the same user — the agent's
//! own `run_shell` included — and the screen credential is exactly what separates the owner's click
//! from everything else the agent can reach (opening a terminal, switching bypass on). macOS and
//! Windows have the same shape: `ps eww <pid>` and Process Explorer's Environment tab read the
//! start-up block of a same-user process. Removing a variable afterwards edits a copy.
//!
//! A pipe has none of that. The bytes exist only between the two ends, are consumed by the one
//! reader, and nothing remains to be read afterwards: no file (so no permission window, no cleanup,
//! no leftover after a crash), no command line (`ps`, crash reports) and no environment block. The
//! backend reads one line, and the write end is closed here, so no grandchild can inherit a live
//! channel either.
//!
//! The marker variable below says "look on stdin"; it carries no secret. Without it (`chimera app`
//! in a terminal, `chimera service start`) the backend keeps its old rule — see
//! `chimera/launch_secrets.py` for the fallback and the risk it leaves.

use std::io::Write;
use std::process::{Child, Command, Stdio};

use serde_json::{Map, Value};

/// Non-secret: tells the backend to read its secrets from stdin. Mirrors `launch_secrets.MARKER_ENV`.
pub const MARKER_ENV: &str = "CHIMERA_LAUNCH_SECRETS";
/// Names the secrets used to travel under. Scrubbed from the child's environment so a value the
/// shell itself inherited (a developer's exported `CHIMERA_SERVER_TOKEN`) is not passed on beside
/// the channel; the one that is meant to arrive does so on stdin.
const SECRET_ENV: [&str; 3] = [
    "CHIMERA_SERVER_TOKEN",
    "CHIMERA_SCREEN_TOKEN",
    "CHIMERA_SCREEN_LAUNCHER_TOKEN",
];

/// What the backend is given. `None` is "not given": the backend then applies its own default
/// (a service with no token of its own uses its folder's `service.token`).
#[derive(Default)]
pub struct Secrets<'a> {
    pub server_token: Option<&'a str>,
    pub screen_token: Option<&'a str>,
    pub launcher_key: Option<&'a str>,
}

impl Secrets<'_> {
    /// The one line, newline included. JSON so a value with any byte in it cannot end the line early.
    pub fn line(&self) -> String {
        let mut map = Map::new();
        for (key, value) in [
            ("server_token", self.server_token),
            ("screen_token", self.screen_token),
            ("launcher_key", self.launcher_key),
        ] {
            if let Some(value) = value {
                map.insert(key.to_string(), Value::String(value.to_string()));
            }
        }
        let mut line = Value::Object(map).to_string();
        line.push('\n');
        line
    }
}

/// Prepare `cmd` to receive the channel: the marker, a piped stdin, and none of the old variables.
pub fn arm(cmd: &mut Command) {
    for name in SECRET_ENV {
        cmd.env_remove(name);
    }
    cmd.env(MARKER_ENV, "stdin").stdin(Stdio::piped());
}

/// Write `line` to the child and close its stdin. A line is far below a pipe's buffer, so this never
/// blocks. A child that has already died makes the write fail; that is not reported here, because
/// the caller is already waiting for the child to announce itself and says why it did not.
pub fn deliver(child: &mut Child, line: &str) {
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(line.as_bytes()).and_then(|()| stdin.flush());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_line_is_one_json_object_and_carries_only_what_was_given() {
        let line = Secrets {
            server_token: Some("srv\"tok\n"),
            screen_token: Some("scr"),
            launcher_key: None,
        }
        .line();
        assert!(line.ends_with('\n') && line.matches('\n').count() == 1, "{line:?}");
        let parsed: Value = serde_json::from_str(line.trim_end()).expect("valid JSON");
        assert_eq!(parsed["server_token"], "srv\"tok\n");
        assert_eq!(parsed["screen_token"], "scr");
        assert!(parsed.get("launcher_key").is_none(), "an absent secret must stay absent");
    }

    #[test]
    fn arming_a_command_removes_the_old_variables_and_sets_only_the_marker() {
        let mut cmd = Command::new("backend");
        cmd.env("CHIMERA_SCREEN_TOKEN", "leftover");
        arm(&mut cmd);
        let envs: Vec<_> = cmd.get_envs().collect();
        let value_of = |name: &str| {
            envs.iter().find(|(k, _)| *k == name).map(|(_, v)| v.map(|v| v.to_string_lossy().into_owned()))
        };
        assert_eq!(value_of(MARKER_ENV), Some(Some("stdin".to_string())));
        for name in SECRET_ENV {
            assert_eq!(value_of(name), Some(None), "{name} would still reach the child's environment");
        }
    }
}
