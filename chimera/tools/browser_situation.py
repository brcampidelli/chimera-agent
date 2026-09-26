"""The browser situation (study 25, S11): what the loop is told, and what the harness enforces.

`bench/PLAN-study25-system-prompts.md` §7 S11 splits the situation in two, and this module holds both
halves so a reader sees each rule next to the mechanism that backs it:

- **What the model is told** — :data:`BROWSER_SITUATION_PROMPT`, the L1 module. The loop adds it only
  when the session holds the browser and ``CHIMERA_BROWSER_SITUATION`` is on
  (:meth:`chimera.core.agent.Agent.compose_system_prompt`).
- **What the harness does whatever the model does** — :class:`BrowserSituation`, which a
  `BrowserTool` built under the same setting runs every action through:
  - a page that needs the person (a sign-in, a two-step code, a captcha, a payment step) becomes a
    typed :class:`Wall`, which the loop takes out of band and ends the run on, as ``handover``;
  - typing into a password, one-time-code or card field hands over instead of typing;
  - reading cookies, site storage or saved passwords through the tool is refused.

**Detection is deterministic and reads the page, never the model.** The rendered DOM is scanned for
three kinds of evidence: input fields by their type, ``autocomplete`` and name; the frames of known
captcha and card-field providers by their address; and the markers of a bot-challenge interstitial.
An input counts only when the page script stamped it with a ref, because the stamp is given only to
elements with a box at snapshot time (`browser_playwright._TAG_SCRIPT`): a login dialog hidden in the
DOM of an ordinary page does not stop anything. Frames carry no such stamp, so a provider's frame is
matched only in its visible form (a reCAPTCHA or hCaptcha checkbox, not their invisible variants).

**What it cannot see.** A wall drawn inside a cross-origin frame the list above does not name, a
login that asks for the account first and the password on the next page (it hands over on that next
page, or on the identity providers named in :data:`_SIGN_IN_HOSTS`), and a wall the page builds after
the snapshot without any later action looking again. A probe that fails is logged and treated as no
wall: the stop is a courtesy to the person, and the boundary is governance, not this scan.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlsplit

from chimera.telemetry import get_logger
from chimera.tools.base import refusal
from chimera.tools.browser import BrowserDriver

_log = get_logger("tools.browser_situation")

#: The L1 browser module (study 25, S11). Every rule carries its reason, because a model that knows
#: why a rule exists applies it to the case the rule did not name.
#:
#: Its benefit is unmeasured. Its harm was checked (`bench/browser_situation`, 24 browsing tasks, k=2):
#: 44/48 on against 45/48 off, b − c = 1, no stop, no refused form, and a prompt cost below the pages'
#: own spread. The "not both" rule did not reduce the read-then-read_text sequence it names (3 on, 1
#: off) — too few to say more, and a reason not to claim it works. The last three rules are backed in
#: the harness by this module and by governance (`chimera.governance.confirmations`), so the sentence
#: is a courtesy to the model, never the only thing between a page and the person's accounts.
BROWSER_SITUATION_PROMPT = (
    "This session has a web browser. Using it well:\n"
    "- To see where you are, take the one cheapest look that answers the question: the element list "
    "that every action already returns, or a search of the page text. Not both, since each look "
    "sends the page again.\n"
    "- Act on the page you were last shown, by its refs, because a ref from an earlier page may now "
    "point at something else.\n"
    "- Do not reopen the address you are already on: a reload repeats the request and can submit a "
    "form twice.\n"
    "- To reach a page, try one address you have reason to expect, then one search. If both miss, "
    "say so rather than guessing more addresses, which mostly lead to error pages or look-alike "
    "sites.\n"
    "- Decide that an action worked from one signal that proves it, such as the confirmation text "
    "or the new address, and stop checking once you have it.\n"
    "- A sign-in, a two-step code, a captcha or a payment is the person's to complete. Stop there "
    "and tell them what the page asks for and what you did before it: you hold none of their "
    "credentials, and passing those steps is their decision.\n"
    "- Never read cookies, site storage or saved passwords, because they are the person's access to "
    "their accounts.\n"
    "- Typing into a page sends what you type to that site. Treat personal details as something you "
    "are sending, and type them only where the request asked you to."
)

WallKind = Literal["login", "two_factor", "captcha", "payment"]

#: How each kind is named to a person and to the model. Fixed words: nothing in a handover's text is
#: read off the page except its address, so a page cannot write its own handover.
_LABELS: dict[str, str] = {
    "login": "a sign-in",
    "two_factor": "a two-step verification code",
    "captcha": "a captcha",
    "payment": "a payment step",
}


@dataclass(frozen=True)
class Wall:
    """A page that needs the person, found in the page itself.

    ``evidence`` is built from fixed words plus a ref or a provider's host — never from page text —
    because it travels outside the data fence: into the loop's closing message and the run's answer.
    ``typed`` says the wall was met by a ``type`` call the tool then refused, so nothing was typed.
    """

    kind: WallKind
    evidence: str
    url: str = ""
    typed: bool = False

    @property
    def label(self) -> str:
        return _LABELS[self.kind]

    def describe(self) -> str:
        """One clause naming the page and the wall, for the model and for the person."""
        return f"{_display(self.url)} asks for {self.label} ({self.evidence})"

    def observation(self) -> str:
        """What the tool returns instead of the page: the model reads this, not the page."""
        typed = " Nothing was typed." if self.typed else ""
        return (
            f"handover: {self.describe()}.{typed} That step is the person's to complete, so the "
            "browser stopped here. Tell them what the page asks for and what you did before it."
        )

    def for_person(self) -> str:
        """The line the run's answer opens with, so every surface shows it whatever the model wrote."""
        typed = " Nothing was typed into it." if self.typed else ""
        return f"Handed over to you: {self.describe()}. I stopped there.{typed}"


def _display(url: str) -> str:
    """The page's address as the handover names it: host and a clipped path, no query.

    The address is the one thing in a handover the page chose, and a handover is read outside the
    data fence. A query string or a long path can carry sentences, so neither travels whole.
    """
    parts = urlsplit(url)
    if not parts.hostname:
        return "the current page"
    path = parts.path if len(parts.path) <= 60 else parts.path[:60] + "…"
    return f"{parts.scheme}://{parts.hostname}{path}"


# --- reading the page ---------------------------------------------------------------------------

#: Element ids of bot-challenge interstitials: Cloudflare's "Just a moment…" page and PerimeterX's
#: press-and-hold button. `bench/browser_element_list` met Cloudflare's on two of twenty live pages.
_CHALLENGE_IDS = frozenset(
    {"challenge-form", "challenge-stage", "challenge-running", "cf-challenge-running", "px-captcha"}
)
#: Set by Cloudflare's challenge script on the interstitial itself, and nowhere else.
_CHALLENGE_SCRIPT_MARK = "_cf_chl_opt"

#: Whole hosts that are sign-in pages. They cover the flows that ask for the account first and show
#: no password field until the next page.
_SIGN_IN_HOSTS = frozenset(
    {
        "accounts.google.com",
        "login.microsoftonline.com",
        "login.live.com",
        "appleid.apple.com",
        "idmsa.apple.com",
        "login.yahoo.com",
        "signin.aws.amazon.com",
    }
)

_OTP_NAME = re.compile(
    r"(?<![a-z])(otp|totp|2fa|mfa|passcode|one[-_ ]?time[-_ ]?(code|password|passcode|pin)"
    r"|verification[-_ ]?code|auth(entication|enticator)?[-_ ]?code"
    r"|c[oó]digo[-_ ]?de[-_ ]?verifica[cç][aã]o)(?![a-z])"
)
_CARD_NAME = re.compile(
    r"card[-_ ]?number|cardnumber|cc[-_ ]?num(ber)?|n[uú]mero[-_ ]?do[-_ ]?cart[aã]o"
    r"|(?<![a-z])(cvc|cvv2?|csc)(?![a-z])"
)
_CARD_AUTOCOMPLETE = ("cc-number", "cc-csc", "cc-exp")


class _PageScan(HTMLParser):
    """One pass over the rendered DOM, keeping only what a wall is recognised by."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        #: Inputs the page script stamped with a ref: the ones with a box at the last snapshot.
        self.fields: list[dict[str, str]] = []
        self.frames: list[dict[str, str]] = []
        self.challenge = False
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "input" and "data-chimera-ref" in values:
            self.fields.append(values)
        elif tag == "iframe":
            self.frames.append(values)
        elif tag == "script":
            self._in_script = True
        if values.get("id", "").lower() in _CHALLENGE_IDS:
            self.challenge = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and _CHALLENGE_SCRIPT_MARK in data:
            self.challenge = True


def _scan(html: str) -> _PageScan:
    scan = _PageScan()
    scan.feed(html)
    scan.close()
    return scan


def field_kind(attrs: dict[str, str]) -> WallKind | None:
    """What a single input field is a wall for, from its attributes, or None for an ordinary field."""
    kind = attrs.get("type", "").lower()
    auto = attrs.get("autocomplete", "").lower()
    named = " ".join(attrs.get(k, "") for k in ("name", "id", "aria-label", "placeholder")).lower()
    if kind == "password" or "current-password" in auto or "new-password" in auto:
        return "login"
    if "one-time-code" in auto or _OTP_NAME.search(named):
        return "two_factor"
    if any(token.startswith(_CARD_AUTOCOMPLETE) for token in auto.split()) or _CARD_NAME.search(named):
        return "payment"
    return None


def _frame_wall(attrs: dict[str, str]) -> tuple[WallKind, str] | None:
    """A known captcha or card-field provider, from a frame's address; its invisible forms excluded."""
    src = attrs.get("src", "")
    parts = urlsplit(src)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    rest = f"{parts.query}#{parts.fragment}".lower()
    if host.removeprefix("www.") in ("google.com", "recaptcha.net") and "/recaptcha/" in path:
        if path.endswith("/anchor") and "size=invisible" not in rest:
            return "captcha", "a reCAPTCHA checkbox"
        return None
    if host.endswith("hcaptcha.com") and "frame=checkbox" in rest and "size=invisible" not in rest:
        return "captcha", "an hCaptcha checkbox"
    if host == "challenges.cloudflare.com":
        return "captcha", "a Cloudflare Turnstile check"
    if host.endswith(("arkoselabs.com", "funcaptcha.com")):
        return "captcha", "an Arkose challenge"
    if host.endswith("captcha-delivery.com"):
        return "captcha", "a DataDome challenge"
    if host == "js.stripe.com" and re.search(r"elements-inner-(card|payment)", path):
        return "payment", "a Stripe card field"
    if "braintreegateway.com" in host and "hosted-fields" in path:
        return "payment", "a Braintree card field"
    if "adyen.com" in host and "securedfields" in path:
        return "payment", "an Adyen card field"
    return None


#: Kinds in the order a page is reported by: a captcha cannot be passed at all, a code must come
#: from the person's own device, a payment spends their money, and a sign-in uses their account.
_ORDER: tuple[WallKind, ...] = ("captcha", "two_factor", "payment", "login")

_FIELD_WORDS: dict[str, str] = {
    "login": "a password field",
    "two_factor": "a one-time code field",
    "payment": "a card field",
}


def detect_wall(html: str, url: str = "") -> Wall | None:
    """The wall ``html`` shows, or None for an ordinary page. Pure: the page and its address only."""
    scan = _scan(html)
    found: dict[str, str] = {}
    if scan.challenge:
        found["captcha"] = "a bot-check page"
    for attrs in scan.frames:
        hit = _frame_wall(attrs)
        if hit is not None:
            found.setdefault(hit[0], hit[1])
    for attrs in scan.fields:
        kind = field_kind(attrs)
        if kind is not None:
            found.setdefault(kind, f"{_FIELD_WORDS[kind]} ({attrs.get('data-chimera-ref', '')})")
    host = (urlsplit(url).hostname or "").lower()
    if host in _SIGN_IN_HOSTS:
        found.setdefault("login", "an identity provider's sign-in page")
    for kind in _ORDER:
        if kind in found:
            return Wall(kind, found[kind], url)
    return None


def wall_for_typing(html: str, ref: str, url: str = "") -> Wall | None:
    """The wall a ``type`` into ``ref`` would cross — a password, code or card field — or None."""
    for attrs in _scan(html).fields:
        if attrs.get("data-chimera-ref") == ref:
            kind = field_kind(attrs)
            if kind is None:
                return None
            return Wall(kind, f"{_FIELD_WORDS[kind]} ({ref})", url, typed=True)
    return None


# --- private stores -----------------------------------------------------------------------------

#: Action names that would read a store if the tool ever grew them. None exists today; naming them
#: keeps a future action (or a model inventing one) from reaching a store through a generic path.
_STORE_ACTIONS = frozenset(
    {
        "cookies", "get_cookies", "storage", "local_storage", "session_storage", "storage_state",
        "evaluate", "eval", "execute_script", "javascript", "passwords",
    }
)
#: Schemes that run a script in a page or open a browser's own pages, which is where cookies, site
#: storage and saved passwords are read. `check_url` already refuses every non-http(s) scheme; this
#: refuses them first, with the reason.
_STORE_SCHEMES = frozenset(
    {
        "javascript", "chrome", "chrome-extension", "edge", "brave", "opera", "vivaldi", "about",
        "devtools", "view-source", "moz-extension", "file",
    }
)
#: Password managers' web vaults: http(s), so nothing above stops them.
_VAULTS: tuple[tuple[str, str], ...] = (
    ("passwords.google.com", ""),
    ("vault.bitwarden.com", ""),
    ("vault.bitwarden.eu", ""),
    ("my.1password.com", ""),
    ("my.1password.eu", ""),
    ("my.1password.ca", ""),
    ("app.dashlane.com", ""),
    ("pass.proton.me", ""),
    ("lastpass.com", "/vault"),
    ("keepersecurity.com", "/vault"),
)


def private_store_refusal(action: str, kwargs: dict[str, Any]) -> str | None:
    """The refusal for a call that would read cookies, site storage or saved passwords, else None."""
    what = ""
    if action.lower() in _STORE_ACTIONS:
        what = f"the action {action!r}"
    url = str(kwargs.get("url", "") or "").strip()
    if not what and url:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower().removeprefix("www.")
        if scheme in _STORE_SCHEMES:
            what = f"a {scheme}: address"
        elif any(host == vault and parts.path.lower().startswith(prefix) for vault, prefix in _VAULTS):
            what = f"a password manager's vault ({host})"
    if not what:
        return None
    return refusal(
        f"[browser: private store — {what}] The browser does not read cookies, site storage or "
        "saved passwords, and does not run scripts in a page or open the browser's own pages, "
        "which is how those are read: they are the person's access to their accounts. The tool "
        "did not run. Do not report this as done."
    )


# --- the harness half ---------------------------------------------------------------------------

#: Actions after which the page on screen may be a different one. `read` is here because a page can
#: change on its own (a sign-in dialog opening after a delay), and a fresh look is when that shows.
_PAGE_ACTIONS = frozenset({"navigate", "click", "back", "read", "type", "scroll"})
#: Reads that load a page only when they carry a ``url``.
_URL_ACTIONS = frozenset({"read_text", "find", "screenshot"})

Dispatch = Callable[[str, BrowserDriver, dict[str, Any]], str]


def _url_of(driver: BrowserDriver) -> str:
    """The loaded page's address when the driver can say (`PlaywrightDriver.url`), else ""."""
    try:
        return str(getattr(driver, "url", "") or "")
    except Exception:  # noqa: BLE001 — an address is a courtesy in the handover's text
        return ""


class BrowserSituation:
    """The harness half of the browser situation, held by one `BrowserTool` (``situation=``).

    Every action goes through :meth:`act`. A wall is kept until the loop takes it
    (:meth:`take_handover`), which is how the loop learns of it: out of band, so neither a governance
    wrapper that fences the observation nor a page that imitates one can change whether the run
    stops. One per tool, and a tool is one conversation's: two runs sharing one browser would share
    its page as well, which is the larger problem.
    """

    def __init__(self) -> None:
        self._handover: Wall | None = None

    def take_handover(self) -> Wall | None:
        """The wall the last action stopped at, once; None when there was none."""
        wall, self._handover = self._handover, None
        return wall

    def act(
        self, action: str, driver: BrowserDriver, kwargs: dict[str, Any], dispatch: Dispatch
    ) -> str:
        refused = private_store_refusal(action, kwargs)
        if refused is not None:
            return refused
        if action == "type":
            ref = str(kwargs.get("ref", "")).strip()
            wall = self._probe(lambda: wall_for_typing(driver.page_html(), ref, _url_of(driver)))
            if wall is not None:
                return self._hand_over(wall)
        result = dispatch(action, driver, kwargs)
        loaded = action in _PAGE_ACTIONS or (
            action in _URL_ACTIONS and str(kwargs.get("url", "") or "").strip()
        )
        if not loaded or result.startswith("error:"):
            return result
        wall = self._probe(lambda: detect_wall(driver.page_html(), _url_of(driver)))
        return self._hand_over(wall) if wall is not None else result

    def _hand_over(self, wall: Wall) -> str:
        self._handover = wall
        _log.info("browser handover: %s", wall.describe())
        return wall.observation()

    @staticmethod
    def _probe(look: Callable[[], Wall | None]) -> Wall | None:
        try:
            return look()
        except Exception as exc:  # noqa: BLE001 — a failed look is no wall; see the module docstring
            _log.debug("browser wall probe skipped: %s", exc)
            return None
