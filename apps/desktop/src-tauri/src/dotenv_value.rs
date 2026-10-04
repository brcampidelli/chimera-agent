//! The value of one `.env` assignment, read by the rule the backend writes it with.
//!
//! The backend writes every value through `chimera/api/key_vault.py::encode_env_value`: bare when a
//! bare value reads back literally, otherwise single-quoted with `\\` and `\'` escaped — the two
//! escapes python-dotenv decodes inside single quotes. This reader follows the same rule, so the
//! shell reads `CHIMERA_SERVER_TOKEN` exactly as the backend (python-dotenv, pydantic-settings)
//! does. The cases both sides must agree on live in one fixture, `fixtures/dotenv_values.tsv`,
//! read by pytest (`tests/test_every_env_reader_reads_back_what_was_written.py`) and by the test
//! below. Std only, so `rustc --test` can run it without building the app.

/// One right-hand side (everything after the first `=`), decoded.
///
/// * single-quoted: `\\` and `\'` decoded, any other backslash kept, ends at the first unescaped
///   quote, and nothing after it counts;
/// * double-quoted (only a hand edit writes one): to the next quote, as before;
/// * bare: up to whitespace followed by `#` (an inline comment), trailing whitespace dropped.
pub fn dotenv_value(raw: &str) -> String {
    let text = raw.trim();
    if let Some(rest) = text.strip_prefix('\'') {
        let mut out = String::new();
        let mut chars = rest.chars().peekable();
        while let Some(c) = chars.next() {
            if c == '\\' {
                if let Some(&next) = chars.peek() {
                    if next == '\\' || next == '\'' {
                        out.push(next);
                        chars.next();
                        continue;
                    }
                }
                out.push(c);
                continue;
            }
            if c == '\'' {
                break;
            }
            out.push(c);
        }
        return out;
    }
    if let Some(rest) = text.strip_prefix('"') {
        return rest.split('"').next().unwrap_or("").to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let mut end = chars.len();
    for i in 1..chars.len() {
        if chars[i] == '#' && chars[i - 1].is_whitespace() {
            end = i - 1;
            break;
        }
    }
    chars[..end].iter().collect::<String>().trim_end().to_string()
}

#[cfg(test)]
mod tests {
    use super::dotenv_value;

    const FIXTURE: &str = include_str!("../fixtures/dotenv_values.tsv");

    fn unhex(hex: &str) -> String {
        let bytes: Vec<u8> = (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("hex"))
            .collect();
        String::from_utf8(bytes).expect("utf-8")
    }

    /// Every case the backend's encoder writes reads back as the value it was given — the same
    /// fixture pytest holds python-dotenv, pydantic-settings and the backend's own reader to.
    #[test]
    fn every_written_value_reads_back_byte_for_byte() {
        let mut cases = 0;
        for line in FIXTURE.lines() {
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (hex, encoded) = line.split_once('\t').expect("hex<TAB>encoded");
            assert_eq!(dotenv_value(encoded), unhex(hex), "case {encoded:?}");
            cases += 1;
        }
        assert!(cases >= 20, "the fixture lost its cases");
    }

    #[test]
    fn a_hand_written_line_still_reads_as_before() {
        assert_eq!(dotenv_value("\"quoted value\""), "quoted value");
        assert_eq!(dotenv_value("'single'"), "single");
        assert_eq!(dotenv_value("abc # a note"), "abc");
        assert_eq!(dotenv_value("  spaced  "), "spaced");
        assert_eq!(dotenv_value("'it\\'s' # after"), "it's");
    }
}
