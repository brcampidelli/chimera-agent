"""The hands-free addressee question, shadow-only until its own bench earns review."""

from __future__ import annotations

from chimera.decisions.contract import Answer, Choice
from chimera.decisions.spec import DecisionSpec, Escalation, Mode, register

DECISION = "voice.addressee"

ADDRESSEE = Choice(
    key="addressee",
    instructions=(
        "Is this utterance addressed to the assistant, such that it should be treated as a message "
        "for it? Judge the speaker's intended addressee, not whether the words contain a question, "
        "command, second-person pronoun, or assistant name. Quoted, broadcast, read-aloud, and "
        "self-directed speech is not addressed to the assistant unless the surrounding utterance "
        "clearly directs it here."
    ),
    options=("for_me", "not_for_me"),
    criteria={
        "for_me": "The speaker intends the assistant to hear and respond to this utterance.",
        "not_for_me": (
            "The utterance is side-talk, broadcast, self-talk, quotation, or reading not directed "
            "to the assistant."
        ),
    },
    event=("for_me",),
    event_name="addressed_to_assistant",
)

SPEC = register(
    DecisionSpec(
        name=DECISION,
        questions=(ADDRESSEE,),
        escalation=Escalation.ANNOTATE,
        bench="bench/voice_addressee/PREREGISTRATION.md",
        threshold=None,
        mode=Mode.SHADOW,
        on_no_signal=None,
        description="Is a hands-free transcript addressed to the assistant? Log only; never gate.",
        surfaces=("chimera/api/code_api.py",),
    )
)

# Deliberately disabled: the separately preregistered measurement and review must precede calls.
SHADOW_ENABLED = False

def decide_shadow(message: str, settings: object) -> Answer:
    """Ask and record one shadow reading; callers must guard this with ``SHADOW_ENABLED``."""
    from chimera.decisions.factory import build_decider

    decider = build_decider(settings)
    return decider.decide(DECISION, message, ADDRESSEE)


def shadow_receipt(message: str, settings: object) -> dict[str, object] | None:
    """Return a log-only receipt; a decision failure adds no action to the voice turn."""
    try:
        answer = decide_shadow(message, settings)
    except Exception:  # noqa: BLE001 — shadow must never interfere with the spoken turn
        return None
    return answer.receipt()


__all__ = ["ADDRESSEE", "DECISION", "SHADOW_ENABLED", "SPEC", "shadow_receipt"]
