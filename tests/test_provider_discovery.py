"""A key for any provider LiteLLM supports must start the agent — not only the five with fields.

The bug these cover: LiteLLM is a hard dependency reaching 100+ vendors, and Chimera used to accept
five. Someone holding a valid Groq or Mistral key was told "No provider key configured" and never
reached LiteLLM at all — a refusal indistinguishable, from the outside, from a broken product.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.config_api import is_editable, patch_config
from chimera.config import Settings, _export_env_file_credentials, get_settings
from chimera.providers import discovery
from chimera.providers.discovery import (
    env_file_credentials,
    generic_providers,
    litellm_known,
    provider_from_env_var,
)
from chimera.providers.gateway import LLMGateway, MissingCredentialsError, _is_local_model


class TestNaming:
    def test_a_provider_key_names_its_provider(self) -> None:
        assert provider_from_env_var("GROQ_API_KEY") == "groq"
        assert provider_from_env_var("MISTRAL_API_KEY") == "mistral"
        assert provider_from_env_var("TOGETHERAI_API_KEY") == "togetherai"

    def test_the_five_with_fields_are_not_reported_twice(self) -> None:
        # They are listed from their own settings fields, first and in a fixed order.
        assert provider_from_env_var("OPENROUTER_API_KEY") is None
        assert provider_from_env_var("DEEPSEEK_API_KEY") is None

    def test_a_speech_key_is_not_a_model_provider(self) -> None:
        # The case that makes the denylist mandatory rather than tidy: both of these ARE entries in
        # LiteLLM's provider enum, so without it someone whose only credential is text-to-speech
        # would pass the credential gate and `doctor` would name them as a source of models.
        assert provider_from_env_var("ELEVENLABS_API_KEY") is None
        assert provider_from_env_var("STABILITY_API_KEY") is None
        assert provider_from_env_var("TAVILY_API_KEY") is None

    def test_anything_that_is_not_a_key_is_not_a_provider(self) -> None:
        assert provider_from_env_var("PATH") is None
        assert provider_from_env_var("CHIMERA_DEFAULT_MODEL") is None
        assert provider_from_env_var("groq_api_key") is None  # lowercase is not an env-var name


class TestDiscovery:
    def test_finds_a_provider_nobody_configured_in_code(self) -> None:
        assert generic_providers({"GROQ_API_KEY": "gsk-x"}) == ["groq"]

    def test_an_empty_value_is_not_a_key(self) -> None:
        # `.env.example` ships `OPENROUTER_API_KEY=` and `chimera init` copies it, so blank values
        # are the normal state of a fresh install rather than an edge case.
        assert generic_providers({"GROQ_API_KEY": "", "MISTRAL_API_KEY": "   "}) == []

    def test_the_order_is_stable(self) -> None:
        # Not cosmetic: `catalog._reachable` compares a model slug's first segment against this list,
        # so an unstable order would make tier resolution unstable with it.
        env = {"XAI_API_KEY": "a", "GROQ_API_KEY": "b", "MISTRAL_API_KEY": "c"}
        assert generic_providers(env) == ["groq", "mistral", "xai"]


class TestLiteLLMRecognition:
    """`doctor` says which discovered names LiteLLM actually knows — a typo looks like a provider."""

    def test_real_providers_are_recognised(self) -> None:
        # This is the assertion that catches the shape of `litellm.provider_list` changing under us.
        # It caught it once already: the members are `str`-mixin enums, which keep `Enum.__hash__`,
        # so comparing them to plain strings silently reported EVERY provider as unknown.
        verdict = litellm_known(["groq", "mistral"])
        assert verdict == {"groq": True, "mistral": True}

    def test_a_typo_is_flagged_rather_than_trusted(self) -> None:
        assert litellm_known(["grok"]) == {"grok": False}

    def test_an_unavailable_litellm_says_nothing_rather_than_something_wrong(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(discovery, "_litellm_providers", lambda: frozenset())
        assert discovery.litellm_known(["groq"]) == {}


class TestSettings:
    def test_a_groq_key_alone_opens_the_gate(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
        get_settings.cache_clear()
        settings = Settings()
        assert settings.has_any_key() is True
        assert settings.configured_providers() == ["groq"]

    def test_the_five_come_first(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        get_settings.cache_clear()
        assert Settings().configured_providers() == ["openai", "groq"]

    def test_no_key_at_all_is_still_no_key(self) -> None:
        assert Settings().has_any_key() is False


class TestDotEnvPassthrough:
    """Opening the gate is useless on its own if the key came from the file we tell people to use."""

    def test_a_key_only_in_the_env_file_reaches_the_environment(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # `Settings` is `extra="ignore"`, so this key is read, matched to no field, and dropped: it
        # becomes neither an attribute nor an environment variable, and LiteLLM never sees it.
        env_file = tmp_path / ".env"
        env_file.write_text("GROQ_API_KEY=gsk-from-file\n", encoding="utf-8")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": env_file})

        _export_env_file_credentials()

        import os

        assert os.environ["GROQ_API_KEY"] == "gsk-from-file"

    def test_the_process_environment_wins_over_the_file(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        # The same precedence pydantic-settings applies to every field it does know about.
        env_file = tmp_path / ".env"
        env_file.write_text("GROQ_API_KEY=from-file\n", encoding="utf-8")
        monkeypatch.setenv("GROQ_API_KEY", "from-shell")
        monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": env_file})

        _export_env_file_credentials()

        import os

        assert os.environ["GROQ_API_KEY"] == "from-shell"

    def test_only_provider_keys_are_exported(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "GROQ_API_KEY=gsk-x\nTAVILY_API_KEY=tv-x\nCHIMERA_HOST_EXEC=allow\n", encoding="utf-8"
        )
        assert env_file_credentials(env_file) == {"GROQ_API_KEY": "gsk-x"}

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        # Configuration discovery must never be the reason a command cannot start.
        assert env_file_credentials(tmp_path / "nope.env") == {}
        assert env_file_credentials(None) == {}

    def test_several_env_files_follow_pydantic_precedence(self, tmp_path: Path) -> None:
        # `SettingsConfigDict` allows a sequence, where the LAST file wins. Getting this backwards
        # would hand LiteLLM a key the rest of the configuration considers overridden.
        first, second = tmp_path / "a.env", tmp_path / "b.env"
        first.write_text("GROQ_API_KEY=old\nMISTRAL_API_KEY=m\n", encoding="utf-8")
        second.write_text("GROQ_API_KEY=new\n", encoding="utf-8")
        assert env_file_credentials([first, second]) == {"GROQ_API_KEY": "new", "MISTRAL_API_KEY": "m"}


class TestGate:
    def test_a_local_runtime_still_needs_no_key(self) -> None:
        # LM Studio, vLLM and llamafile are the same situation as Ollama and were being refused a
        # key none of them wants.
        assert _is_local_model("lm_studio/qwen") is True
        assert _is_local_model("hosted_vllm/llama") is True
        assert _is_local_model("llamafile/mistral") is True
        assert _is_local_model("groq/llama-3.3-70b") is False

    def test_a_discovered_key_passes_the_gate(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
        get_settings.cache_clear()
        LLMGateway()._require_credentials("groq/llama-3.3-70b")  # must not raise

    def test_the_refusal_names_the_provider_asked_for(self, monkeypatch: Any) -> None:
        gw = LLMGateway()
        monkeypatch.setattr(type(gw.settings), "has_any_key", lambda _self: False)
        with pytest.raises(MissingCredentialsError) as excinfo:
            gw._require_credentials("groq/llama-3.3-70b")
        message = str(excinfo.value)
        # Reciting the five Chimera has fields for is how the old message taught people their Groq
        # key was unsupported, when it was merely unlisted.
        assert "GROQ_API_KEY" in message
        assert "groq" in message

    def test_a_bare_model_name_does_not_invent_a_variable(self, monkeypatch: Any) -> None:
        gw = LLMGateway()
        monkeypatch.setattr(type(gw.settings), "has_any_key", lambda _self: False)
        with pytest.raises(MissingCredentialsError) as excinfo:
            gw._require_credentials("gpt-5.5")
        assert "GPT-5.5_API_KEY" not in str(excinfo.value)


class TestWriteSurface:
    def test_the_screen_can_save_what_the_gate_accepts(self) -> None:
        # A gate that accepts a Groq key and a settings screen that refuses to store one would tell
        # the user their key is unsupported while the agent is busy using it.
        assert is_editable("GROQ_API_KEY") is True
        assert is_editable("OPENROUTER_API_KEY") is True
        assert is_editable("CHIMERA_HOST_EXEC") is True
        assert is_editable("CHIMERA_TAINT_NARROW") is False
        assert is_editable("PATH") is False

    def test_a_discovered_key_is_persisted(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        patch_config({"GROQ_API_KEY": "gsk-written"}, env_path=env_file)
        assert "GROQ_API_KEY=gsk-written" in env_file.read_text(encoding="utf-8")

    def test_a_newline_is_still_rejected(self, tmp_path: Path) -> None:
        # Allowlisting the key is not enough: a newline in the value splits into extra .env lines.
        with pytest.raises(ValueError):
            patch_config({"GROQ_API_KEY": "a\nCHIMERA_SANDBOX=local"}, env_path=tmp_path / ".env")
