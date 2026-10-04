//! Reading the backend from the shell: one GET over loopback, the token it may need, and the few
//! facts the tray shows (pending approvals, running turns, today's spend).
//!
//! Hand-rolled HTTP/1.0 over a `TcpStream`, for the reason `address_of` in main.rs gives for parsing
//! by hand: the backend is on 127.0.0.1, the requests are three fixed GETs, and an HTTP client crate
//! would be a new dependency through the supply-chain audit for less code than this file.
//!
//! **The token.** `/api/approvals`, `/api/usage` and `/api/code/turns/running` sit behind the
//! backend's guard, which is a no-op until the owner sets `CHIMERA_SERVER_TOKEN` (there is a Row for
//! it in Settings). From that day a shell that sends no token gets 401 on every look — and a tray
//! that read 401 as "nothing pending, nothing spent" would be the comfortable, false screen. So the
//! token is read where the backend reads it, and a 401 that still happens is surfaced, never turned
//! into a zero.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::time::Duration;

use serde_json::Value;

/// The variable the backend's guard compares against (`chimera/api/app.py`, `_require_token`).
pub const TOKEN_VAR: &str = "CHIMERA_SERVER_TOKEN";

/// The token the backend is enforcing right now, as the backend itself would find it.
///
/// The backend is `pydantic-settings` over a `.env` RELATIVE TO ITS WORKING DIRECTORY — which is
/// this shell's, since the sidecar inherits it — and a real environment variable beats the file
/// (`chimera/config.py` documents that precedence). Both are read on every call, not once: the
/// token can be set from Settings while the app runs (`PATCH /api/config` writes `.env` and the
/// backend re-reads it), and a copy taken at startup would be the stale one.
///
/// The study's critic said "the `.env` of the data dir". It is not there: `CHIMERA_HOME` points
/// into the data dir, but `env_file=".env"` is resolved against the CWD, which for an installed
/// app is the install directory (the Start-menu shortcut's "Start in").
pub fn token_now() -> Option<String> {
    let from_env = std::env::var(TOKEN_VAR).ok();
    let dotenv = std::env::current_dir()
        .ok()
        .and_then(|cwd| std::fs::read_to_string(cwd.join(".env")).ok());
    server_token(from_env, dotenv.as_deref())
}

/// The precedence, pure: the environment wins over the file, and an empty value means "no token",
/// which is how the backend's guard reads it too (`if not token: return`).
pub fn server_token(from_env: Option<String>, dotenv: Option<&str>) -> Option<String> {
    if let Some(value) = from_env {
        // An empty variable still WINS over the file (pydantic takes it), and it means no token.
        let value = value.trim().to_string();
        return (!value.is_empty()).then_some(value);
    }
    dotenv.and_then(token_in_dotenv)
}

/// `CHIMERA_SERVER_TOKEN` out of a `.env` body, the way python-dotenv reads it: comments and blank
/// lines skipped, an optional `export `, optional matching quotes, the LAST assignment wins, and the
/// name compared case-insensitively (the settings model is `case_sensitive=False`).
fn token_in_dotenv(body: &str) -> Option<String> {
    let mut found: Option<String> = None;
    for line in body.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let line = line.strip_prefix("export ").unwrap_or(line);
        let Some((name, value)) = line.split_once('=') else { continue };
        if !name.trim().eq_ignore_ascii_case(TOKEN_VAR) {
            continue;
        }
        // The backend's own rule (`key_vault.encode_env_value`): single-quoted values carry
        // escapes, and a reader that stopped at the first quote would read a different token.
        let value = crate::dotenv_value::dotenv_value(value);
        found = Some(value);
    }
    found.filter(|v| !v.is_empty())
}

/// What one look at the backend came back with.
#[derive(Debug, PartialEq)]
pub enum Fetch {
    Json(Value),
    /// 401: the backend wants a token this shell did not have, or had wrong. Its own case because
    /// it is the one failure the owner can fix, and the one that must never read as "nothing".
    Unauthorized,
    /// Anything else — refused, timed out, a 5xx, a body that is not JSON. The text says which.
    Failed(String),
}

/// `GET {origin}{path}` with the bearer token when there is one.
///
/// HTTP/1.0 on purpose: the server then frames the body by closing the connection (or by
/// `Content-Length`), never with chunked encoding, so "read to the end" is the whole protocol. The
/// chunked decoder below is there anyway, because a reply is parsed for what it IS, not for what
/// the request hoped it would be.
pub fn get_json(origin: &str, path: &str, token: Option<&str>, timeout: Duration) -> Fetch {
    let hostport = origin
        .strip_prefix("http://")
        .unwrap_or(origin)
        .trim_end_matches('/')
        .to_string();
    let Some(addr) = super_addr(&hostport) else {
        return Fetch::Failed(format!("cannot resolve {hostport}"));
    };
    let mut stream = match TcpStream::connect_timeout(&addr, Duration::from_millis(500)) {
        Ok(s) => s,
        Err(e) => return Fetch::Failed(format!("connect: {e}")),
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    let auth = token.map(|t| format!("Authorization: Bearer {t}\r\n")).unwrap_or_default();
    let request = format!(
        "GET {path} HTTP/1.0\r\nHost: {hostport}\r\nAccept: application/json\r\n{auth}Connection: close\r\n\r\n"
    );
    if let Err(e) = stream.write_all(request.as_bytes()) {
        return Fetch::Failed(format!("send: {e}"));
    }
    let mut raw = Vec::new();
    if let Err(e) = stream.read_to_end(&mut raw) {
        return Fetch::Failed(format!("read: {e}"));
    }
    match parse_response(&raw) {
        Ok((401, _)) => Fetch::Unauthorized,
        Ok((200, body)) => serde_json::from_slice::<Value>(&body)
            .map(Fetch::Json)
            .unwrap_or_else(|e| Fetch::Failed(format!("body: {e}"))),
        Ok((status, _)) => Fetch::Failed(format!("HTTP {status}")),
        Err(e) => Fetch::Failed(e),
    }
}

fn super_addr(hostport: &str) -> Option<std::net::SocketAddr> {
    use std::net::ToSocketAddrs;
    hostport.to_socket_addrs().ok()?.next()
}

/// Status code and body out of a raw HTTP/1.x response.
pub fn parse_response(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("no end of headers")?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| "headers are not text")?;
    let body = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status = lines
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or("no status line")?;
    let chunked = lines.any(|l| {
        l.split_once(':').is_some_and(|(k, v)| {
            k.trim().eq_ignore_ascii_case("transfer-encoding")
                && v.trim().eq_ignore_ascii_case("chunked")
        })
    });
    if chunked {
        return dechunk(body).map(|b| (status, b));
    }
    Ok((status, body.to_vec()))
}

fn dechunk(mut rest: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let eol = rest.windows(2).position(|w| w == b"\r\n").ok_or("truncated chunk size")?;
        let size_text = std::str::from_utf8(&rest[..eol]).map_err(|_| "bad chunk size")?;
        let size_text = size_text.split(';').next().unwrap_or("").trim();
        let size = usize::from_str_radix(size_text, 16).map_err(|_| "bad chunk size")?;
        rest = &rest[eol + 2..];
        if size == 0 {
            return Ok(out);
        }
        if rest.len() < size + 2 {
            return Err("truncated chunk".into());
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
}

/// The ids of the approvals waiting for the owner (`GET /api/approvals`): every item in the list.
///
/// Every item, because that is what the list IS. `pending()` (chimera/governance/pending.py) returns
/// one entry per `<id>.ask.json`, and an answer never marks that file: it is written beside it as
/// `<id>.answer.json`, and the turn that asked deletes both when it collects it — within one of its
/// own polls. The item's `decision` field is NOT an answer. It is the level of the verdict that
/// raised the question (`block` | `review` | `warn`, default `review`), always present and never
/// empty; the first version of this function read it as "already decided" and therefore returned
/// nothing for every real question, so the flash it exists for never fired. The test below reads
/// the backend's own schema to keep that from coming back.
///
/// What this counts that is not strictly waiting: a question answered in the last moment before its
/// turn collected it, and the question of a turn that died, which stays until the backend's sweep.
/// The window's own list shows both too, so the tray says what the window would say.
pub fn pending_ids(approvals: &Value) -> Vec<String> {
    approvals
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|q| q.get("id").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Whether the set of waiting questions is different from the last one seen — a question arrived or
/// one went away. Order is not a change: the backend sorts by level, then age.
pub fn changed(before: &[String], now: &[String]) -> bool {
    before.len() != now.len() || now.iter().any(|id| !before.contains(id))
}

/// Whether anything in `now` has not been flashed for yet.
///
/// Flash once per question, not once per poll: the informational request on Windows flashes the
/// taskbar button a few times and leaves it highlighted until the window is activated, so asking
/// again every three seconds would only restart the flashing — and an owner who looked, decided to
/// answer later and went back to work must not be called again for the same question.
pub fn has_new(flashed: &[String], now: &[String]) -> bool {
    now.iter().any(|id| !flashed.contains(id))
}

/// How many coding turns are running (`GET /api/code/turns/running`, the list the status bar reads).
pub fn running_count(turns: &Value) -> Option<usize> {
    turns.as_array().map(Vec::len)
}

/// What was spent on `day` ("YYYY-MM-DD") according to `GET /api/usage`. A day with no row spent 0.
///
/// `day` is a UTC date, and that is the backend's convention, not a choice made here: `by_day` keys
/// are the first ten characters of each record's ISO timestamp (`chimera/api/usage.py`), and the
/// bridge's `spent_today_usd` uses the UTC date too. The tray says "(UTC)" rather than pretend.
pub fn spent_on(usage: &Value, day: &str) -> Option<f64> {
    let days = usage.get("by_day")?.as_array()?;
    let row = days.iter().find(|d| d.get("day").and_then(Value::as_str) == Some(day));
    Some(row.and_then(|r| r.get("usd")).and_then(Value::as_f64).unwrap_or(0.0))
}

/// The UTC date of a moment, as "YYYY-MM-DD", without a date crate.
///
/// Howard Hinnant's `civil_from_days`, which is exact over the whole proleptic Gregorian calendar.
pub fn utc_day(unix_seconds: u64) -> String {
    let days = (unix_seconds / 86_400) as i64;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

/// The UTC date right now.
pub fn utc_today() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    utc_day(secs)
}

#[cfg(test)]
mod tests {
    use super::{
        changed, get_json, has_new, parse_response, pending_ids, running_count, server_token,
        spent_on, utc_day, Fetch,
    };
    use serde_json::json;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::mpsc;
    use std::time::Duration;

    /// The environment wins over the file, as in pydantic-settings — and an empty variable still
    /// wins, meaning "no token", which is how the guard reads it.
    #[test]
    fn the_token_is_found_where_the_backend_finds_it() {
        let file = "CHIMERA_SERVER_TOKEN=from-file\n";
        assert_eq!(server_token(Some("from-env".into()), Some(file)).as_deref(), Some("from-env"));
        assert_eq!(server_token(None, Some(file)).as_deref(), Some("from-file"));
        assert_eq!(server_token(Some(String::new()), Some(file)), None);
        assert_eq!(server_token(None, None), None);
        assert_eq!(server_token(None, Some("OTHER=1\n")), None);
    }

    /// The token line as the backend writes it, for every case of the shared fixture: what the
    /// tray sends is what the backend's guard compares against.
    #[test]
    fn the_token_reads_back_as_the_backend_wrote_it() {
        let fixture = include_str!("../fixtures/dotenv_values.tsv");
        for line in fixture.lines().filter(|l| !l.is_empty() && !l.starts_with('#')) {
            let (hex, encoded) = line.split_once('\t').expect("hex<TAB>encoded");
            let bytes: Vec<u8> = (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("hex"))
                .collect();
            let value = String::from_utf8(bytes).expect("utf-8");
            let body = format!("OTHER=1\nCHIMERA_SERVER_TOKEN={encoded}\nAFTER='x'\n");
            let want = (!value.is_empty()).then_some(value);
            assert_eq!(server_token(None, Some(&body)), want, "case {encoded:?}");
        }
    }

    #[test]
    fn a_dotenv_is_read_the_way_python_dotenv_reads_it() {
        let read = |body: &str| server_token(None, Some(body));
        assert_eq!(read("# CHIMERA_SERVER_TOKEN=commented\n").as_deref(), None);
        assert_eq!(read("export CHIMERA_SERVER_TOKEN=abc\n").as_deref(), Some("abc"));
        assert_eq!(read("CHIMERA_SERVER_TOKEN=\"quoted value\"\n").as_deref(), Some("quoted value"));
        assert_eq!(read("CHIMERA_SERVER_TOKEN='single'\n").as_deref(), Some("single"));
        assert_eq!(read("CHIMERA_SERVER_TOKEN=abc # a note\n").as_deref(), Some("abc"));
        assert_eq!(read("chimera_server_token=lower\n").as_deref(), Some("lower"));
        // The last assignment wins — `_write_env_var` rewrites in place, but a hand edit may append.
        assert_eq!(read("CHIMERA_SERVER_TOKEN=old\nCHIMERA_SERVER_TOKEN=new\n").as_deref(), Some("new"));
        // An empty value is no token, not the empty-string token.
        assert_eq!(read("CHIMERA_SERVER_TOKEN=\n").as_deref(), None);
        assert_eq!(read("CHIMERA_SERVER_TOKEN=x\r\nOTHER=y\r\n").as_deref(), Some("x"));
    }

    #[test]
    fn a_response_yields_its_status_and_body() {
        let raw = b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n[]";
        assert_eq!(parse_response(raw).unwrap(), (200, b"[]".to_vec()));
        let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\n[1,\r\n2\r\n2]\r\n0\r\n\r\n";
        assert_eq!(parse_response(chunked).unwrap(), (200, b"[1,2]".to_vec()));
        assert!(parse_response(b"garbage").is_err());
    }

    /// One canned reply from a real socket, and the request the shell actually sent.
    fn serve_once(reply: &'static str) -> (String, mpsc::Receiver<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let (mut conn, _) = listener.accept().unwrap();
            let mut buf = [0u8; 4096];
            let n = conn.read(&mut buf).unwrap();
            tx.send(String::from_utf8_lossy(&buf[..n]).into_owned()).unwrap();
            conn.write_all(reply.as_bytes()).unwrap();
        });
        (origin, rx)
    }

    /// The regression the critic named: with a token configured, the shell must SEND it — and a
    /// 401 must come back as its own answer, never as an empty list.
    #[test]
    fn the_token_is_sent_and_a_refusal_is_not_read_as_nothing() {
        let (origin, sent) = serve_once("HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\n[]");
        let got = get_json(&origin, "/api/approvals", Some("s3cret"), Duration::from_secs(5));
        assert_eq!(got, Fetch::Json(json!([])));
        let request = sent.recv().unwrap();
        assert!(request.starts_with("GET /api/approvals HTTP/1.0\r\n"), "{request}");
        assert!(request.contains("Authorization: Bearer s3cret\r\n"), "the token was not sent: {request}");

        let (origin, sent) = serve_once("HTTP/1.1 401 Unauthorized\r\ncontent-length: 26\r\n\r\n{\"detail\":\"unauthorized\"}\n");
        let got = get_json(&origin, "/api/approvals", None, Duration::from_secs(5));
        assert_eq!(got, Fetch::Unauthorized, "a 401 must not become an answer");
        assert!(!sent.recv().unwrap().contains("Authorization"), "no token, no header");
    }

    #[test]
    fn a_backend_that_is_not_there_is_a_failure_with_a_reason() {
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        match get_json(&format!("http://127.0.0.1:{port}"), "/api/approvals", None, Duration::from_secs(1)) {
            Fetch::Failed(why) => assert!(why.starts_with("connect"), "{why}"),
            other => panic!("a closed port read as {other:?}"),
        }
    }

    /// Fixtures in the shape `GET /api/approvals` really has: every item carries a `decision`, and
    /// it is the LEVEL that raised the question, never the owner's answer. All three are waiting.
    #[test]
    fn every_question_the_backend_lists_is_waiting_whatever_its_level() {
        let list = json!([
            {"id": "a", "action": "shell: ls", "reason": "r", "asked_at": 1.0, "age_seconds": 2.0, "decision": "review"},
            {"id": "b", "action": "shell: rm", "reason": "r", "asked_at": 1.0, "age_seconds": 2.0, "decision": "block"},
            {"id": "c", "action": "write_file: x", "reason": "r", "asked_at": 1.0, "age_seconds": 2.0, "decision": "warn"},
        ]);
        assert_eq!(pending_ids(&list), vec!["a".to_string(), "b".to_string(), "c".to_string()]);
        assert!(pending_ids(&json!([])).is_empty());
        assert!(pending_ids(&json!({"detail": "x"})).is_empty());
    }

    /// The fixture above is tied to the backend: if `decision` stops being the level with a
    /// "review" default, or the list grows a field that says a question was answered, this turns
    /// red and `pending_ids` has to be looked at again — instead of a made-up schema passing.
    #[test]
    fn the_fixture_is_the_backends_own_schema() {
        let schemas = include_str!("../../../../chimera/api/schemas.py");
        let approval_out = schemas
            .split_once("class ApprovalOut(BaseModel):")
            .expect("ApprovalOut is still in chimera/api/schemas.py")
            .1
            .split("
class ")
            .next()
            .unwrap_or("");
        assert!(
            approval_out.contains("decision: str = \"review\"  # the level of the verdict that raised it"),
            "ApprovalOut.decision is no longer the level with a review default"
        );
        // Field declarations only (`    name: type`), not the docstrings that talk about answers.
        let fields: Vec<&str> = approval_out
            .lines()
            .filter(|l| l.starts_with("    ") && !l.starts_with("     "))
            .filter_map(|l| l.trim().split_once(':').map(|(name, _)| name))
            .filter(|name| name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') && !name.is_empty())
            .collect();
        assert!(fields.contains(&"id") && fields.contains(&"decision"), "the field reader found {fields:?}");
        for name in &fields {
            assert!(
                !name.contains("answer") && !name.contains("approved"),
                "ApprovalOut gained `{name}`: answered questions can now be told apart, and pending_ids must use it"
            );
        }
        let pending = include_str!("../../../../chimera/governance/pending.py");
        assert!(
            pending.contains("decision=str(data.get(\"decision\") or \"review\")"),
            "pending() no longer fills decision with the level"
        );
        assert!(
            pending.contains("directory.glob(\"*.ask.json\")") && pending.contains("{request_id}.answer.json"),
            "pending() no longer lists the ask files with answers kept beside them"
        );
    }

    #[test]
    fn a_change_in_the_waiting_set_is_seen_but_not_its_order() {
        let ids = |v: &[&str]| v.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        assert!(!changed(&ids(&[]), &ids(&[])));
        assert!(changed(&ids(&[]), &ids(&["a"])), "a question arrived");
        assert!(changed(&ids(&["a", "b"]), &ids(&["a"])), "a question went away");
        assert!(!changed(&ids(&["a", "b"]), &ids(&["b", "a"])), "the order is the backend's sort");
    }

    #[test]
    fn a_question_is_flashed_for_once() {
        let ids = |v: &[&str]| v.iter().map(|s| (*s).to_string()).collect::<Vec<_>>();
        assert!(has_new(&ids(&[]), &ids(&["a"])));
        assert!(!has_new(&ids(&["a"]), &ids(&["a"])), "the same question flashed twice");
        assert!(has_new(&ids(&["a"]), &ids(&["a", "b"])));
        assert!(!has_new(&ids(&["a", "b"]), &ids(&[])), "nothing pending is nothing to flash");
    }

    #[test]
    fn todays_spend_and_the_running_count_come_from_the_lists() {
        let usage = json!({"by_day": [{"day": "2026-10-02", "usd": 1.5}, {"day": "2026-10-03", "usd": 0.42}]});
        assert_eq!(spent_on(&usage, "2026-10-03"), Some(0.42));
        assert_eq!(spent_on(&usage, "2026-10-04"), Some(0.0), "a day with no row spent nothing");
        assert_eq!(spent_on(&json!({}), "2026-10-03"), None, "no by_day is not the same as zero");
        assert_eq!(running_count(&json!([{}, {}])), Some(2));
        assert_eq!(running_count(&json!({"detail": "x"})), None);
    }

    #[test]
    fn the_utc_date_is_exact() {
        assert_eq!(utc_day(0), "1970-01-01");
        assert_eq!(utc_day(951_782_400), "2000-02-29"); // a leap day in a century leap year
        assert_eq!(utc_day(1_790_000_000), "2026-09-21");
        // The last second of that day is still that day; the next one is not.
        let end = 1_790_000_000 - 1_790_000_000 % 86_400 + 86_399;
        assert_eq!(utc_day(end), "2026-09-21");
        assert_eq!(utc_day(end + 1), "2026-09-22");
        assert_eq!(utc_day(4_102_444_800), "2100-01-01");
    }
}
