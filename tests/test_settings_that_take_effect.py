"""A setting the screen says it saved has to be the one the next request reads.

`live_settings()` exists for exactly this, and its own docstring states the rule: "Saving a setting
that silently does nothing is worse than not offering it, because the confirmation is what turns it
into a lie." It also carries a deliberate escape hatch — an explicitly injected `Settings` stays
frozen, because that is what a test or a bench comparing two configurations is asking for.

`chimera app` was passing one. The object it passed IS `get_settings()`, so the injection changed
nothing except to take the escape hatch on every desktop launch: `live_settings()` returned the boot
photograph for the life of the process, and the rows fed by it — the reach floor, the deployment
denylist, `/api/vision`'s default model — kept answering with the launch value while the Settings
screen said "saved".
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.config import Settings, get_settings


def _client(tmp_path: Path, **kwargs: object) -> TestClient:
    """Built the way `chimera app` builds it — which is the configuration that shipped."""
    return TestClient(build_api_app(lambda: None, workspace=tmp_path, **kwargs))  # type: ignore[arg-type]


def _default_model(client: TestClient) -> str:
    """Read through a real route, not through a private closure.

    `GET /api/models` resolves its answer from `live_settings()`, so it reports what a request would
    actually get — which is the only thing the user cares about and the thing that was wrong.
    """
    return str(client.get("/api/models").json()["default"])


def test_the_desktop_app_reads_settings_as_they_are_now(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "before/model")
    get_settings.cache_clear()

    client = _client(tmp_path)  # no `settings=`, exactly as `chimera app` now calls it
    assert _default_model(client) == "before/model"

    # What `PATCH /api/config` does: write the value, drop the cache, report success.
    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "after/model")
    get_settings.cache_clear()

    assert _default_model(client) == "after/model", (
        "the screen said saved; the next request must read the saved value"
    )


def test_an_explicitly_injected_settings_still_stays_frozen(tmp_path: Path, monkeypatch) -> None:
    """The escape hatch is load-bearing and stays.

    A bench comparing two configurations hands the app one and means it. Thirty-two tests in this
    suite pass `settings=` for the same reason.
    """
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    pinned = Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_DEFAULT_MODEL="pinned/model"
    )

    client = _client(tmp_path, settings=pinned)

    monkeypatch.setenv("CHIMERA_DEFAULT_MODEL", "after/model")
    get_settings.cache_clear()

    assert _default_model(client) == "pinned/model", "passing one means USE THIS"


def test_the_app_does_not_hand_the_api_a_frozen_settings(tmp_path: Path) -> None:
    """The structural half, because the behavioural test above can only see the app it builds.

    The defect was one keyword argument at one call site in `chimera app`, and nothing in the two
    tests above would notice it coming back — they construct their own app. This reads the call
    site itself. `chimera app` may pass anything to `build_api_app` except a `settings=`, because
    that argument means "frozen" and the desktop is the one caller that must not be.
    """
    import re

    source = (Path(__file__).resolve().parents[1] / "chimera" / "cli" / "main.py").read_text(
        encoding="utf-8"
    )
    call = re.search(r"api = build_api_app\((.*?)\n    \)", source, re.S)
    assert call is not None, "the app's build_api_app call moved — update this guard"
    body = re.sub(r"#[^\n]*", "", call.group(1))  # the explanation names it; the code must not
    assert "settings=" not in body, (
        "chimera app is passing settings= again, which freezes live_settings() for the whole "
        "process and makes every Settings row fed by it a confirmation that does nothing"
    )


# --- the fusion cast, which the app photographs at boot -------------------------------------------


class _Echo:
    """A backend that answers every stage, so a run completes without a provider."""

    def __init__(self) -> None:
        self.models: list[str] = []

    def complete(self, messages, *, model=None, **kwargs):  # type: ignore[no-untyped-def]
        from chimera.providers.gateway import CompletionResult

        self.models.append(str(model))
        return CompletionResult(content="ok", model=str(model), prompt_tokens=1, completion_tokens=1)


def test_a_saved_fusion_cast_reaches_the_next_run(tmp_path: Path, monkeypatch) -> None:
    """"Saved. New conversations start with this cast." — it did not.

    The app builds ONE `FusionEngine` at boot and keeps it for the process, and `FusionConfig` is a
    plain dataclass with no lazy re-read. So the panel landed in `.env`, the screen confirmed, and
    every fused turn until the next relaunch still used the launch cast.
    """
    from chimera.fusion.engine import FusionEngine

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "a/one,b/two")
    get_settings.cache_clear()

    backend = _Echo()
    engine = FusionEngine(backend)  # built once, as the app builds it
    engine.run([{"role": "user", "content": "hi"}])
    assert "a/one" in backend.models and "b/two" in backend.models

    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "c/three,d/four")
    get_settings.cache_clear()
    backend.models.clear()

    engine.run([{"role": "user", "content": "hi"}])

    assert "c/three" in backend.models and "d/four" in backend.models
    assert "a/one" not in backend.models, "the launch cast must not outlive the save"


def test_a_pinned_cast_is_never_re_read(tmp_path: Path, monkeypatch) -> None:
    """`_cast_for_turn` overlays a request's own cast, and `fusion_for_role` builds a role's panel.

    Both hand the engine a config and mean it. A re-read would silently replace a caller's explicit
    choice with the machine's default — which is the same class of bug in the other direction.
    """
    from chimera.fusion.engine import FusionConfig, FusionEngine

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_FUSION_PANEL", "env/one,env/two")
    get_settings.cache_clear()

    backend = _Echo()
    engine = FusionEngine(
        backend,
        FusionConfig(panel=["pinned/one", "pinned/two"], judge="j/model", synthesizer="s/model"),
    )

    engine.run([{"role": "user", "content": "hi"}])

    assert "pinned/one" in backend.models
    assert "env/one" not in backend.models
