"""An approval typed into the chat bot is the owner's, once, and never reaches the model.

Study 29 P3.3. The approval webhook put the question in the owner's pocket and the answer still
needed `chimera approve` in a terminal. With ``CHIMERA_APPROVE_VIA_CHAT`` on, the question carries a
one-time code and the bot accepts ``aprovar <id> <code>``. Approvals are the owner's decision, so
almost every test here is a refusal: wrong code, reused code, another request's code, an expired
request, a sender outside the allowlist, a bot (including this bot, i.e. a model speaking through
``send_message``), the setting off, an empty allowlist, a rate-limited sender. Each of them must
leave the question unanswered AND produce no turn — an approval-shaped message never reaches a
session, so the code never reaches the model, its history or memory.

The questions are asked through the real `pending.ask_durably`, on a fake clock, and answered from
inside its ``deliver`` callback — which is what a person with the chat open does, and what makes
these round trips rather than tests of a helper.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.governance.pending import answer, answer_with_code, ask_durably, history, pending
from chimera.server import DiscordAdapter, SignalAdapter, SlackAdapter, TelegramAdapter
from chimera.server.chat_approval import (
    NEUTRAL,
    ChatApprovals,
    enabled_platforms,
    offers_chat_code,
    parse,
    startup_warning,
)
from chimera.server.gateway import InboundMessage, MessageGateway

OWNER = "111"
CHAT = "chan-1"
_ENVS = (
    "CHIMERA_APPROVE_VIA_CHAT",
    "CHIMERA_APPROVAL_WEBHOOK",
    "CHIMERA_DISCORD_ALLOWED_USERS",
    "CHIMERA_TELEGRAM_ALLOWED_USERS",
    "CHIMERA_SLACK_ALLOWED_USERS",
    "CHIMERA_SIGNAL_ALLOWED_USERS",
    "CHIMERA_WHATSAPP_ALLOWED_NUMBERS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENVS:
        monkeypatch.delenv(name, raising=False)


def _settings(home: Path, *, on: bool = True, discord: str = OWNER, **env: str) -> Settings:
    return Settings(
        _env_file=None,  # the worktree's own .env must not decide who is listed
        CHIMERA_HOME=str(home),
        CHIMERA_APPROVE_VIA_CHAT="on" if on else "off",
        CHIMERA_DISCORD_ALLOWED_USERS=discord,
        **env,
    )


class _Relogio:
    """A clock and a sleep that advance together, so a wait costs no wall time."""

    def __init__(self) -> None:
        self.agora = 0.0

    def __call__(self) -> float:
        return self.agora

    def sleep(self, segundos: float) -> None:
        self.agora += segundos


class _Webhook:
    """Stands in for the owner's approval webhook: carries the code, then runs what the owner does."""

    offers_chat_code = True

    def __init__(self, act: Callable[[str], None]) -> None:
        self.act = act
        self.texts: list[str] = []
        self.error: BaseException | None = None

    def __call__(self, text: str) -> None:
        self.texts.append(text)
        try:
            self.act(text)
        except BaseException as exc:
            # `ask_durably` swallows a failed delivery on purpose (a dead webhook must not fail the
            # run), which would swallow an assertion made inside `act` too. Kept and re-raised.
            self.error = exc
            raise


def _ask(home: Path, act: Callable[[str], None], *, wait: float = 60.0) -> bool:
    relogio = _Relogio()
    webhook = _Webhook(act)
    result = ask_durably(
        home, "git push --force origin main", "policy: force push",
        deliver=webhook, wait_seconds=wait, poll_seconds=1,
        clock=relogio, sleep=relogio.sleep,
    )
    if webhook.error is not None:
        raise webhook.error
    return result


def _id_code(text: str) -> tuple[str, str]:
    match = re.search(r"aprovar ([0-9a-f]{12}) (\d{6})", text)
    assert match, f"the delivered question carries no code: {text!r}"
    return match[1], match[2]


class _Sessions:
    """A session factory that records every session it was asked for, and every message sent."""

    def __init__(self) -> None:
        self.created = 0
        self.sent: list[str] = []

    def __call__(self) -> Any:
        self.created += 1
        outer = self

        class _Session:
            max_turns = 50

            def send(self, text: str) -> str:
                outer.sent.append(text)
                return "a turn ran"

        return _Session()


def _gateway(settings: Settings, home: Path, **kw: Any) -> tuple[MessageGateway, _Sessions]:
    sessions = _Sessions()
    approvals = ChatApprovals(settings, home, **kw)
    return MessageGateway(sessions, intercept=approvals.intercept), sessions


def _msg(text: str, *, user: str = OWNER, platform: str = "discord", from_bot: bool = False) -> InboundMessage:
    return InboundMessage(text=text, chat_id=CHAT, platform=platform, user=user, from_bot=from_bot)


# ------------------------------------------------------------------ the happy path


def test_the_owner_approves_with_the_code_and_the_waiting_call_proceeds(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []
    codes: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        codes.append(code)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    assert _ask(tmp_path, owner) is True
    assert replies[0].startswith("Approved")
    assert codes[0] not in replies[0], "the confirmation must not echo the code"
    assert sessions.created == 0, "an answer became a turn"
    line = history(tmp_path)[-1]
    assert line["outcome"] == "approved"
    assert line["answered_via"] == f"discord:{CHAT}"


def test_the_owner_refuses_with_the_code(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        assert gateway.on_message(_msg(f"recusar {request_id} {code}")).startswith("Refused")

    assert _ask(tmp_path, owner) is False
    assert history(tmp_path)[-1]["outcome"] == "refused"
    assert history(tmp_path)[-1]["answered_via"] == f"discord:{CHAT}"
    assert sessions.created == 0


def test_the_english_verbs_and_a_pasted_code_block_are_accepted(tmp_path: Path) -> None:
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        assert gateway.on_message(_msg(f"  `Approve {request_id.upper()} {code}`  ")).startswith(
            "Approved"
        )

    assert _ask(tmp_path, owner) is True


# ------------------------------------------------------------------ the code


def test_a_wrong_code_answers_nothing_and_the_question_times_out(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def stranger_guess(text: str) -> None:
        request_id, code = _id_code(text)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {wrong}")))

    assert _ask(tmp_path, stranger_guess) is False
    assert replies == [NEUTRAL]
    assert history(tmp_path)[-1]["outcome"] == "timeout"
    assert sessions.created == 0


def test_a_code_works_once(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def owner_twice(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))
        # The same line again, before the asker's poll has picked the answer up: a refusal now
        # must not overwrite the approval, and an approval must not be possible twice.
        replies.append(gateway.on_message(_msg(f"recusar {request_id} {code}")))

    assert _ask(tmp_path, owner_twice) is True
    assert replies[0].startswith("Approved")
    assert replies[1] == NEUTRAL
    assert sessions.created == 0


def test_a_used_code_is_gone_from_the_question(tmp_path: Path) -> None:
    """Consumed on use, not merely shadowed by the answer file: the two layers are separate so that
    neither alone decides whether a code can work twice."""
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        gateway.on_message(_msg(f"aprovar {request_id} {code}"))
        data = json.loads((tmp_path / "approvals" / f"{request_id}.ask.json").read_text("utf-8"))
        assert "code_hash" not in data and "code_used_at" in data

    assert _ask(tmp_path, owner) is True


def test_a_code_cannot_overturn_an_answer_given_elsewhere(tmp_path: Path) -> None:
    """The owner said no in the terminal, then a yes with the (unused) code arrives in the chat: the
    first answer stands. Otherwise the chat could overwrite a refusal before the asker read it."""
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def no_then_yes(text: str) -> None:
        request_id, code = _id_code(text)
        assert answer(tmp_path, request_id, False, via="cli")
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    assert _ask(tmp_path, no_then_yes) is False
    assert replies == [NEUTRAL]
    assert history(tmp_path)[-1]["answered_via"] == "cli"


def test_another_requests_code_does_not_answer_this_one(tmp_path: Path) -> None:
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []
    outcomes: list[bool] = []

    def while_a_is_waiting(text_a: str) -> None:
        _id_a, code_a = _id_code(text_a)

        def answer_b_with_as_code(text_b: str) -> None:
            id_b, _code_b = _id_code(text_b)
            replies.append(gateway.on_message(_msg(f"aprovar {id_b} {code_a}")))

        outcomes.append(_ask(tmp_path, answer_b_with_as_code))

    assert _ask(tmp_path, while_a_is_waiting) is False
    assert outcomes == [False], "B was approved with A's code"
    assert replies == [NEUTRAL]


def test_an_expired_request_cannot_be_answered(tmp_path: Path) -> None:
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    # A zero wait: the question expires the instant it is asked, before the owner can type.
    assert _ask(tmp_path, owner, wait=0.0) is False
    assert replies == [NEUTRAL]


def test_a_request_that_already_timed_out_cannot_be_answered(tmp_path: Path) -> None:
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)
    seen: list[str] = []

    assert _ask(tmp_path, seen.append) is False  # nobody answered: silence refused it
    request_id, code = _id_code(seen[0])

    assert gateway.on_message(_msg(f"aprovar {request_id} {code}")) == NEUTRAL
    assert not (tmp_path / "approvals" / f"{request_id}.answer.json").exists()


def test_a_question_asked_without_a_code_cannot_be_answered_from_the_chat(tmp_path: Path) -> None:
    """The setting off at the asker: no code is issued, so there is nothing a chat could match."""
    relogio = _Relogio()
    texts: list[str] = []
    outcomes: list[str] = []

    def plain_webhook(text: str) -> None:
        texts.append(text)
        request_id = pending(tmp_path)[0].id
        outcomes.append(answer_with_code(tmp_path, request_id, "123456", True, via="discord:x"))

    assert ask_durably(
        tmp_path, "x", "y", deliver=plain_webhook, wait_seconds=10, poll_seconds=5,
        clock=relogio, sleep=relogio.sleep,
    ) is False
    assert "aprovar" not in texts[0] and "chimera approve" in texts[0]
    assert outcomes == ["no_code"]


def test_the_code_is_stored_nowhere_on_disk_not_even_as_a_hash(tmp_path: Path) -> None:
    """Was `test_the_code_is_stored_only_as_a_hash`, which asserted the hash WAS in the file.

    That hash was the weakness (study 30, S30-30): six digits under sha256 invert in a loop of a
    million calls, so whoever could read the question file - the agent's own shell - held the code.
    The code now lives only in the asking process's memory; the file says only that the chat may
    answer (`chat_code`) and until when."""
    import hashlib

    def look(text: str) -> None:
        request_id, code = _id_code(text)
        raw = (tmp_path / "approvals" / f"{request_id}.ask.json").read_text("utf-8")
        data = json.loads(raw)
        assert code not in {str(v) for v in data.values()}
        assert hashlib.sha256(f"{request_id}:{code}".encode()).hexdigest() not in raw
        assert "code_hash" not in data
        assert data.get("chat_code") is True and "expires_at" in data
        # And never on what a screen, the API or the desktop bridge reads back.
        assert code not in repr(pending(tmp_path))

    _ask(tmp_path, look)


# ------------------------------------------------------------------ who may answer


def test_a_sender_outside_the_allowlist_cannot_answer(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def intruder(text: str) -> None:
        request_id, code = _id_code(text)  # even holding the right code
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}", user="999")))

    assert _ask(tmp_path, intruder) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0


def test_a_bot_holding_the_right_code_cannot_answer(tmp_path: Path) -> None:
    """A bot that shares the owner's id string, with the right code — still not a person."""
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def a_bot(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}", from_bot=True)))

    assert _ask(tmp_path, a_bot) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0


def test_the_bots_own_message_never_reaches_the_gateway() -> None:
    """A model that sends the exact format through `send_message` posts AS the bot. The adapters
    drop their own messages before routing, so it is never an inbound message at all — and a bot
    that some adapter does let through arrives marked, for the interceptor to refuse."""
    line = "aprovar 0123456789ab 123456"
    discord = DiscordAdapter("t", allowed_users={OWNER}, respond_to_bots=True)
    assert discord._inbound(
        author_id=OWNER, author_is_bot=True, is_self=True, channel_id=1, content=line
    ) is None
    other_bot = discord._inbound(
        author_id=OWNER, author_is_bot=True, is_self=False, channel_id=1, content=line
    )
    assert other_bot is not None and other_bot.from_bot is True

    telegram = TelegramAdapter("t", allowed_users={OWNER}, respond_to_bots=True)
    update = {"message": {"text": line, "chat": {"id": 5}, "from": {"id": OWNER, "is_bot": True}}}
    tg = telegram._message_from_update(update)
    assert tg is not None and tg.from_bot is True

    slack = SlackAdapter("xoxb", "xapp", allowed_users={OWNER}, respond_to_bots=True)
    sl = slack._message_from_event(
        {"type": "message", "user": OWNER, "bot_id": "B1", "text": line, "channel": "C1"}
    )
    assert sl is not None and sl.from_bot is True

    signal = SignalAdapter("http://bridge", "+15550000000", allowed_users={"+15550000000"})
    own = {"envelope": {"source": "+15550000000", "dataMessage": {"message": line}}}
    assert signal._message_from_envelope(own) is None


def test_a_person_is_not_marked_as_a_bot() -> None:
    discord = DiscordAdapter("t", allowed_users={OWNER})
    person = discord._inbound(
        author_id=OWNER, author_is_bot=False, is_self=False, channel_id=1, content="oi"
    )
    assert person is not None and person.from_bot is False


def test_with_the_setting_off_an_approval_shaped_message_is_neither_an_answer_nor_a_turn(
    tmp_path: Path,
) -> None:
    gateway, sessions = _gateway(_settings(tmp_path, on=False), tmp_path)
    replies: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    assert _ask(tmp_path, owner) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0 and sessions.sent == []


def test_with_an_empty_allowlist_it_is_refused_outright_and_says_so(tmp_path: Path) -> None:
    settings = _settings(tmp_path, discord="")
    gateway, sessions = _gateway(settings, tmp_path)
    replies: list[str] = []

    def anyone(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}", user="anyone")))

    assert _ask(tmp_path, anyone) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0
    warning = startup_warning(settings, "discord")
    assert warning is not None and "CHIMERA_DISCORD_ALLOWED_USERS" in warning
    # And the asker does not even issue a code, because no bot would accept it.
    assert offers_chat_code(settings) is False
    assert enabled_platforms(settings) == set()


def test_the_list_that_counts_is_the_one_for_the_platform_the_answer_came_from(
    tmp_path: Path,
) -> None:
    """Listed on Discord is not listed on Telegram: ids are per platform, and an empty Telegram list
    means that bot is open — so Telegram may not approve even for an id the Discord list carries."""
    gateway, _ = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def via_telegram(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}", platform="telegram")))

    assert _ask(tmp_path, via_telegram) is False
    assert replies == [NEUTRAL]
    assert startup_warning(_settings(tmp_path), "telegram") is not None


def test_whatsapp_is_not_a_surface_that_approves(tmp_path: Path) -> None:
    settings = _settings(tmp_path, CHIMERA_WHATSAPP_ALLOWED_NUMBERS=OWNER)
    gateway, _ = _gateway(settings, tmp_path)
    replies: list[str] = []

    def via_whatsapp(text: str) -> None:
        request_id, code = _id_code(text)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}", platform="whatsapp")))

    assert _ask(tmp_path, via_whatsapp) is False
    assert replies == [NEUTRAL]


# ------------------------------------------------------------------ the rate limit


def test_failed_attempts_are_rate_limited_per_sender(tmp_path: Path) -> None:
    relogio = _Relogio()
    gateway, _ = _gateway(_settings(tmp_path), tmp_path, clock=relogio, max_failures=3, window=600)
    replies: list[str] = []

    def guesser_then_owner(text: str) -> None:
        request_id, code = _id_code(text)
        wrong = f"{(int(code) + 7) % 1_000_000:06d}"
        for _ in range(3):
            replies.append(gateway.on_message(_msg(f"aprovar {request_id} {wrong}")))
        # Limited now: even the RIGHT code is refused unchecked, or the limit only slows a guesser.
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))
        relogio.agora += 601  # the window passes
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    assert _ask(tmp_path, guesser_then_owner) is True
    assert replies[:4] == [NEUTRAL] * 4
    assert replies[4].startswith("Approved")


# ------------------------------------------------------------------ never a turn, never the model


def test_the_code_never_reaches_a_session_the_history_or_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    codes: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        codes.append(code)
        # Every outcome at once: wrong code, then the right one, then a reuse.
        gateway.on_message(_msg(f"aprovar {request_id} {(int(code) + 1) % 1_000_000:06d}"))
        gateway.on_message(_msg(f"aprovar {request_id} {code}"))
        gateway.on_message(_msg(f"aprovar {request_id} {code}"))

    with caplog.at_level(logging.DEBUG):
        assert _ask(tmp_path, owner) is True

    # No session was created, so nothing reached the model, a chat history or memory — all three
    # hang off the session the gateway would have built.
    assert sessions.created == 0 and sessions.sent == []
    assert gateway.active_chats == 0
    code = codes[0]
    assert code not in (tmp_path / "approvals" / "history.jsonl").read_text("utf-8")
    assert code not in caplog.text


def test_a_forwarded_notification_is_held_back_and_not_applied(tmp_path: Path) -> None:
    """The owner forwards the whole question to the bot to ask what it is. The code is on its own
    line inside it: that must not become a turn (the model would read the code), and a message that
    says more than the answer is not an answer."""
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    replies: list[str] = []

    def forward(text: str) -> None:
        replies.append(gateway.on_message(_msg(f"o que é isso?\n\n{text}")))

    assert _ask(tmp_path, forward) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0


def test_an_ordinary_message_is_still_a_turn(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)

    assert gateway.on_message(_msg("approve PR 123456 please")) == "a turn ran"
    assert gateway.on_message(_msg("approve PR 123456")) == "a turn ran"
    assert sessions.sent == ["approve PR 123456 please", "approve PR 123456"]


@pytest.mark.parametrize(
    "text",
    [
        "aprovar 0123456789ab 123456",
        "RECUSAR 0123456789AB 123456",
        "deny 0123456789a 12345",  # a typo'd id and a short code are still somebody pasting a code
        "`approve 0123456789ab 123456`",
        # Compatibility forms read as the answer they look like (NFKC), so they are held back too:
        # the long s once matched the pattern, missed the verb table and stopped the bot.
        "recuſar 0123456789ab 123456",
        "aprovar 0123456789ab １２３４５６",
        "ａｐｐｒｏｖｅ 0123456789ab 123456",
        "deny abcdefabcdef 123456",  # a real id may, rarely, have no digit
    ],
)
def test_anything_shaped_like_an_answer_is_held_back_from_the_model(text: str) -> None:
    assert parse(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "approve PR 123456",
        "aprovar",
        "aprovar 0123456789ab",
        "aprovar o deploy agora",
        # English words spelled in a-f are not ids: no digit, and not twelve characters.
        "deny decade 2024",
        "approve facade 1234",
        "deny the decade 2024",
    ],
)
def test_ordinary_sentences_are_not_answers(text: str) -> None:
    assert parse(text) is None


# ------------------------------------------------------------------ the construction paths


def test_the_apps_bot_installs_the_interceptor(tmp_path: Path) -> None:
    """`MessagingManager` builds the bot the desktop runs; it must refuse the same way `serve` does,
    and before it ever builds a session (the factory would need a working backend)."""
    from chimera.server.manager import MessagingManager

    class _Adapter:
        platform = "discord"

        def send(self, *_a: Any) -> str:
            return "ok"

    settings = _settings(tmp_path, CHIMERA_DISCORD_BOT_TOKEN="t")
    manager = MessagingManager(
        settings=settings, backend=object(), model=None, max_steps=2, workspace=tmp_path,  # type: ignore[arg-type]  # never reached: the interceptor answers first
        adapter_factory=lambda _p: _Adapter(),
    )
    route = manager._gateway_on_message(_Adapter())

    assert route(_msg("aprovar 0123456789ab 123456")) == NEUTRAL


def test_serve_installs_the_interceptor_and_warns_when_it_cannot_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from chimera.cli import main as cli

    intercept = cli._chat_approvals(_settings(tmp_path, discord=""), "discord")

    assert intercept(_msg("aprovar 0123456789ab 123456", user="anyone")) == NEUTRAL
    assert "CHIMERA_DISCORD_ALLOWED_USERS" in capsys.readouterr().out


def test_the_webhook_offers_a_code_only_when_a_bot_would_take_it(tmp_path: Path) -> None:
    from chimera.governance.approval import deliverer_for

    hook = {"CHIMERA_APPROVAL_WEBHOOK": "https://example.invalid/h"}
    on = deliverer_for(_settings(tmp_path, **hook))
    off = deliverer_for(_settings(tmp_path, on=False, **hook))
    open_bot = deliverer_for(_settings(tmp_path, discord="", **hook))

    assert on is not None and on.offers_chat_code is True
    assert off is not None and off.offers_chat_code is False
    assert open_bot is not None and open_bot.offers_chat_code is False
    assert "example.invalid" not in repr(on), "the webhook URL is a credential"


def test_every_answer_says_which_surface_gave_it(tmp_path: Path) -> None:
    def from_the_terminal(_text: str) -> None:
        answer(tmp_path, pending(tmp_path)[0].id, True, via="cli")

    assert _ask(tmp_path, from_the_terminal) is True
    assert history(tmp_path)[-1]["answered_via"] == "cli"


# ------------------------------------------------------------------ the interceptor never stops the bot


@pytest.mark.parametrize(
    "text",
    [
        "recuſar 0123456789ab 123456",
        "aprovar 0123456789ab ١٢٣٤٥٦",  # Arabic-Indic digits
        "İapprove 0123456789ab 123456",
        "deny K0123456789a 123456",
    ],
)
def test_no_message_makes_the_interceptor_raise(tmp_path: Path, text: str) -> None:
    """The reviewer's case: setting OFF (the default), sender in no list. Before re.ASCII, the long
    s matched the shape case-insensitively, then `VERBS["recuſar"]` raised KeyError on the
    Telegram/Signal polling loop and the bot stopped. Whatever the text, intercept returns."""
    for settings in (_settings(tmp_path, on=False, discord=""), _settings(tmp_path)):
        approvals = ChatApprovals(settings, tmp_path)
        for user in ("stranger", OWNER):
            reply = approvals.intercept(_msg(text, user=user))
            assert reply is None or reply == NEUTRAL


@pytest.mark.parametrize(
    "text",
    ["recuſar 0123456789ab 123456", "aprovar 0123456789ab ١٢٣٤٥٦"],
)
def test_the_patterns_themselves_match_only_ascii(text: str) -> None:
    """Below the NFKC step: the patterns are ASCII, so no Unicode case-fold can carry a verb that is
    not in the table to the lookup, and no other script's digits can pass for a code."""
    import chimera.server.chat_approval as chat_approval

    assert chat_approval._SHAPE.fullmatch(text) is None
    assert chat_approval._INSIDE.search(text) is None


def test_the_owner_may_type_the_verb_in_a_compatibility_form(tmp_path: Path) -> None:
    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        assert gateway.on_message(_msg(f"recuſar {request_id} {code}")).startswith("Refused")

    assert _ask(tmp_path, owner) is False
    assert history(tmp_path)[-1]["outcome"] == "refused"
    assert sessions.created == 0


def test_a_defect_in_the_interceptor_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing is approved, nothing becomes a turn, the bot keeps running, and the log names the
    exception type without the text (which might carry the code)."""
    import chimera.server.chat_approval as chat_approval

    def broken(_text: str) -> None:
        raise RuntimeError("boom")

    gateway, sessions = _gateway(_settings(tmp_path), tmp_path)
    monkeypatch.setattr(chat_approval, "parse", broken)
    seen: list[str] = []
    replies: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        seen.append(code)
        replies.append(gateway.on_message(_msg(f"aprovar {request_id} {code}")))

    with caplog.at_level(logging.DEBUG):
        assert _ask(tmp_path, owner) is False
    assert replies == [NEUTRAL]
    assert sessions.created == 0
    assert "RuntimeError" in caplog.text
    assert seen[0] not in caplog.text


# ------------------------------------------------------------------ the last poll interval


def test_an_answer_written_during_the_last_sleep_is_read_not_timed_out(tmp_path: Path) -> None:
    """`chimera approve` (or the chat) answered in the final poll interval and was told so; the wait
    used to end without looking again and record a timeout over it."""
    relogio = _Relogio()

    def sleep(segundos: float) -> None:
        relogio.sleep(segundos)
        if relogio.agora >= 3.0:
            for waiting in pending(tmp_path):
                answer(tmp_path, waiting.id, True, via="cli")

    assert ask_durably(
        tmp_path, "x", "y", wait_seconds=3.0, poll_seconds=1.0, clock=relogio, sleep=sleep
    ) is True
    assert history(tmp_path)[-1]["outcome"] == "approved"


def test_the_code_stops_working_a_poll_interval_before_the_wait_ends(tmp_path: Path) -> None:
    """The code's expiry is wall time and the wait is monotonic; ending the code one interval early
    means no code accepted in the chat can land after the asker has stopped reading."""
    outcomes: list[str] = []

    def owner(text: str) -> None:
        request_id, code = _id_code(text)
        data = json.loads((tmp_path / "approvals" / f"{request_id}.ask.json").read_text("utf-8"))
        asked_at = float(data["asked_at"])
        assert data["expires_at"] == pytest.approx(asked_at + 59.0)
        outcomes.append(
            answer_with_code(tmp_path, request_id, code, True, via="t", now=asked_at + 59.5)
        )
        outcomes.append(
            answer_with_code(tmp_path, request_id, code, True, via="t", now=asked_at + 58.5)
        )

    assert _ask(tmp_path, owner, wait=60.0) is True
    assert outcomes == ["expired", "applied"]
