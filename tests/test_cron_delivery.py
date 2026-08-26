"""Getting a scheduled job's answer to a person.

``deliver_to`` existed on the model from the beginning and appeared in exactly two places in the
whole codebase: its own declaration, and a line copying it into the result file. Nothing read it to
send anything. So a schedule wrote its answer into a JSONL nobody opens, and that was delivery — an
install could run a job every night for a week and its owner never see a word of the output.

These tests run against a real local HTTP server rather than a mocked `urlopen`. The thing under
test is what goes on the wire: the body shape each service expects, the status handling, and the
promise that a chat outage cannot fail a job that already did its work.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from chimera.scheduler.delivery import (
    MAX_CHARS,
    clip,
    deliver_to_webhook,
    payload_for,
    redact,
)


class _Recebedor(BaseHTTPRequestHandler):
    """Records what arrived, and answers with whatever the test asked for."""

    status = 204
    recebidos: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        n = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(n).decode("utf-8")
        type(self).recebidos.append(
            {"path": self.path, "body": json.loads(corpo), "ctype": self.headers.get("Content-Type")}
        )
        self.send_response(type(self).status)
        self.end_headers()
        if type(self).status >= 400:
            self.wfile.write(b"unknown webhook")

    def log_message(self, *_args) -> None:  # keep pytest output readable
        pass


@pytest.fixture
def servidor():
    """A webhook endpoint on a real socket, for the duration of one test."""
    _Recebedor.recebidos = []
    _Recebedor.status = 204
    httpd = HTTPServer(("127.0.0.1", 0), _Recebedor)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}/api/webhooks/1/abc"
    httpd.shutdown()
    httpd.server_close()


def test_the_answer_reaches_the_webhook(servidor) -> None:
    _, url = servidor
    r = deliver_to_webhook(url, "o site tem 3 arquivos")

    assert r.ok, r.detail
    assert len(_Recebedor.recebidos) == 1
    enviado = _Recebedor.recebidos[0]
    assert enviado["body"] == {"content": "o site tem 3 arquivos"}
    assert enviado["ctype"] == "application/json"


def test_slack_gets_the_field_slack_reads(servidor) -> None:
    """Discord reads `content`, Slack reads `text`. Sending the wrong one is a silent 400."""
    assert payload_for("https://hooks.slack.com/services/T/B/x", "oi") == {"text": "oi"}
    assert payload_for("https://discord.com/api/webhooks/1/x", "oi") == {"content": "oi"}
    # Anything else is treated as Discord-compatible, which most self-hosted chat webhooks are.
    assert payload_for("https://chat.exemplo.com/hook/1", "oi") == {"content": "oi"}


def test_a_refused_delivery_is_reported_not_raised(servidor) -> None:
    """The job already ran. Throwing here would discard work that succeeded and would count
    against `consecutive_failures` as though the schedule itself were broken."""
    _, url = servidor
    _Recebedor.status = 404

    r = deliver_to_webhook(url, "texto")

    assert not r.ok
    assert "404" in r.detail
    assert "unknown webhook" in r.detail, "the service's own reason is worth keeping"


def test_an_unreachable_host_is_reported_not_raised() -> None:
    # Port 1 on localhost: nothing listens, and the connection is refused immediately rather than
    # hanging, so this stays a fast test.
    r = deliver_to_webhook("http://127.0.0.1:1/hook", "texto", timeout=2.0)
    assert not r.ok
    assert r.detail


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "javascript:alert(1)", "/hook"])
def test_only_http_is_ever_fetched(url: str) -> None:
    """`deliver_to` is a free-text field on a job an agent can propose. Agent-proposed jobs already
    arrive disabled; this is the second lock, not the first."""
    r = deliver_to_webhook(url, "texto")
    assert not r.ok
    assert "http" in r.detail.lower()


def test_a_long_answer_is_shortened_and_says_so(servidor) -> None:
    """Discord refuses a body over 2000 characters outright — a refused delivery would be worse
    than a shortened one, since the full answer is in the result file either way."""
    _, url = servidor
    r = deliver_to_webhook(url, "x" * 5000)

    assert r.ok
    enviado = _Recebedor.recebidos[0]["body"]["content"]
    assert len(enviado) <= MAX_CHARS
    assert "truncated" in enviado, "a message that merely stops looks like an agent that stopped"


def test_a_short_answer_is_left_exactly_alone() -> None:
    # The control for the test above: a clip that always clips would pass it and mangle every
    # ordinary message.
    assert clip("três linhas curtas") == "três linhas curtas"


# --------------------------------------------------------------------- the sink, and its wiring


def _job(**over):
    from chimera.scheduler import CronJob

    base = {"id": "j1", "name": "resumo do site", "schedule": "0 7 * * *", "action": "liste"}
    return CronJob(**{**base, **over})


def test_the_sink_writes_the_file_even_with_no_webhook(tmp_path) -> None:
    """The file is the record that survives a chat outage, a revoked webhook and a sleeping laptop."""
    from chimera.scheduler.delivery import make_deliver

    alvo = tmp_path / "scheduler" / "cron_results.jsonl"
    make_deliver(alvo)(_job(), "três arquivos")

    linha = json.loads(alvo.read_text(encoding="utf-8").strip())
    assert linha["answer"] == "três arquivos"
    assert linha["deliver_to"] is None
    assert "delivered" not in linha, "a job that asked for no delivery has no delivery to report on"


def test_the_sink_actually_sends_when_the_job_names_a_webhook(tmp_path) -> None:
    """The test the original defect would have survived.

    `deliver_to` was declared on the model and copied into the result record, and nothing ever read
    it to send. Every test of the SENDING mechanism would have passed the whole time — the hole was
    that nobody called it. So this asserts the call.
    """
    from chimera.scheduler.delivery import Delivered, make_deliver

    enviados = []

    def falso_envio(url, texto):
        enviados.append((url, texto))
        return Delivered(True, "HTTP 204")

    alvo = tmp_path / "cron_results.jsonl"
    make_deliver(alvo, send=falso_envio)(_job(deliver_to="https://discord.com/api/webhooks/1/x"), "ok")

    assert len(enviados) == 1, "the sink did not send anything"
    url, texto = enviados[0]
    assert url == "https://discord.com/api/webhooks/1/x"
    assert "resumo do site" in texto, "the job's name tells the reader which schedule spoke"
    assert "ok" in texto

    linha = json.loads(alvo.read_text(encoding="utf-8").strip())
    assert linha["delivered"] is True


def test_a_failed_delivery_is_recorded_and_announced_not_swallowed(tmp_path) -> None:
    """A delivery that fails silently is indistinguishable from a job that never ran — which is the
    confusion this whole area has been producing."""
    from chimera.scheduler.delivery import Delivered, make_deliver

    avisos = []
    alvo = tmp_path / "cron_results.jsonl"
    sink = make_deliver(
        alvo, warn=avisos.append, send=lambda _u, _t: Delivered(False, "HTTP 401: bad token")
    )
    sink(_job(deliver_to="https://discord.com/api/webhooks/1/x"), "a resposta")

    linha = json.loads(alvo.read_text(encoding="utf-8").strip())
    assert linha["delivered"] is False
    assert "401" in linha["delivery_detail"]
    # And the answer is still on disk: the job worked, only the chat service did not.
    assert linha["answer"] == "a resposta"
    assert avisos and "401" in avisos[0]


def test_the_app_uses_this_sink_rather_than_one_of_its_own() -> None:
    """The wiring, asserted at the only place it can be: the command that builds the daemon.

    Read from the module's own source — via `inspect`, not a relative path, which depends on the
    directory pytest was started from — because the sink is built inside a Typer command that needs
    a provider, a workspace and a bound port to run. So this guards the line against being dropped;
    the tests above are what cover the behaviour.
    """
    import inspect

    import chimera.cli.main as cli

    fonte = inspect.getsource(cli)
    assert "make_deliver(" in fonte, "the app no longer builds its sink from this module"
    assert "from chimera.scheduler.delivery import make_deliver" in fonte
    # And it has not grown a second sink of its own beside it, which is how the two would drift.
    assert 'results_path.open("a"' not in fonte, "the app is writing the result file itself again"


def test_the_url_is_never_repeated_in_full() -> None:
    """A webhook URL is a credential: whoever holds it can post in that channel."""
    url = "https://discord.com/api/webhooks/123456789/segredo-que-nao-pode-vazar"
    escondida = redact(url)

    assert "segredo-que-nao-pode-vazar" not in escondida
    assert "123456789" not in escondida
    assert "discord.com" in escondida, "and it still has to be useful for telling hosts apart"
