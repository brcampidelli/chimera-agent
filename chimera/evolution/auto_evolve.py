"""Auto-evolution hook: turn a recurring success into a validated, tested skill.

Fired by the autonomous loop after a verified success. It only acts once a task
pattern has **recurred** (so one-off tasks don't spawn skills), and every candidate
clears two gates before it is kept:

1. **Governance** — the :class:`SkillValidator`'s constrained edit surface rejects an
   unsafe proposal before it is ever run.
2. **Executable smoke test** — the skill must run end-to-end and produce non-empty
   output. This is the verify-or-revert discipline applied to the agent's own skills:
   a proposal that doesn't actually work is discarded, never stored.

Learned skills are prompt templates with no code execution, so creating one is
non-destructive; the gates above keep it honest rather than guarding against harm.
"""

from __future__ import annotations

import hashlib
import re
import string
from typing import TYPE_CHECKING

from chimera.eval.anytime import best_possible_wilson, wilson_lower_best_of
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.holdout import HoldoutGate, HoldoutVerdict
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore
from chimera.governance.validator import SkillValidator
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.evolution.collective import CollectiveSkillEvolver
    from chimera.governance.audit import AuditLog

_log = get_logger("evolution.auto")


def _task_id(task: str) -> str:
    """A stable identity for a TASK, so the holdout can exclude the one the skill came from.

    Hashed rather than slugged: a slug of the first few words collides across tasks that open the
    same way ("fix the failing test in ..."), and a collision here does not fail loudly — it
    silently excludes a case that should have been scored, shrinking the holdout toward the
    unmeasured verdict without anything saying so.
    """
    return hashlib.sha256(" ".join(task.split()).encode("utf-8")).hexdigest()[:16]


def _placeholders(template: str) -> list[str]:
    return [field for _, field, _, _ in string.Formatter().parse(template) if field]


def _assinatura(skill: object) -> set[str]:
    """As palavras que dizem o que uma skill FAZ, sem o nome.

    O nome e' a parte que menos ajuda: tres cartoes para "pagina HTML de arquivo unico a partir de
    um brief" sairam com tres nomes diferentes (`build_standalone_html_from_brief`,
    `brief_to_offline_single_file_page`, `offline_single_file_html_from_brief`) e o mesmo conteudo.
    Comparar descricao + gatilho + acao pega isso; comparar nome nao pega nada.
    """
    partes = " ".join(
        str(getattr(skill, campo, "") or "")
        for campo in ("description", "trigger", "do", "prompt_template")
    ).lower()
    return {t for t in re.findall(r"[a-zà-ÿ]{4,}", partes)}


def _semelhanca(a: set[str], b: set[str]) -> float:
    """Jaccard. Sem modelo e sem embedding: o custo de deduplicar nao pode ser outra chamada paga."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class AutoSkillEvolver:
    """Proposes, gates and stores a learned skill when a task recurs."""

    def __init__(
        self,
        evolver: SkillEvolver,
        store: SkillStore,
        *,
        validator: SkillValidator | None = None,
        min_recurrences: int = 2,
        collective: CollectiveSkillEvolver | None = None,
        min_transfer: float = 0.5,
        accept_mode: str = "point",
        provisional: bool = False,
        audit: AuditLog | None = None,
        holdout: HoldoutGate | None = None,
        dedupe_at: float = 0.72,
    ) -> None:
        self.evolver = evolver
        self.store = store
        self.validator = validator
        self.audit = audit
        # M18-4: when set, a clean-run skill is born 'provisional' (on measured probation) instead of
        # active — the lifecycle policy promotes it once it proves itself, or demotes it if it doesn't.
        self.provisional = provisional
        self.min_recurrences = min_recurrences
        # When a fusion panel is available, prefer a candidate proposed across the
        # panel and kept by cross-model transferability (OpenClaw-Skill) over a
        # single-model proposal. Falls back to single-model when unset.
        self.collective = collective
        self.min_transfer = min_transfer
        #: Acima disto, um candidato e' considerado o mesmo cartao que um ja' guardado e nao entra.
        #: Quatro projetos produziram tres cartoes quase identicos para a mesma tarefa porque nada
        #: olhava a biblioteca antes de escrever nela. Guardar 3 ou 300 e' identico se nenhum e'
        #: lido; guardar 3 iguais e' pior que guardar 1, porque o proximo recall tem de escolher.
        self.dedupe_at = dedupe_at
        # Opt-in, and off changes nothing: without a gate this class behaves exactly as it did.
        # What it adds is the axis the other two gates do not have — the smoke test runs the
        # candidate on its OWN task and checks the output is non-empty, and `min_transfer` varies
        # the MODEL while holding that same task fixed. Neither asks whether the skill works on a
        # task it has never seen. See `chimera/evolution/holdout.py`.
        self.holdout = holdout
        # "point" (raw pass fraction) or "wilson" (lower confidence bound on the
        # fraction) — the honesty upgrade that stops a lucky small-sample pass counting.
        self.accept_mode = accept_mode

    def _warn_if_gate_unsatisfiable(self, k: int) -> None:
        """Say so, loudly, when no result can ever clear the Wilson gate.

        A lower confidence bound on a handful of trials is far below the point estimate, so a
        threshold chosen for a raw fraction can be unreachable: with the default 3-model panel a
        flawless 3/3 scores only 0.439, and a 0.5 threshold rejects it. Left silent, that reads in
        the log as "nothing was good enough" forever, when the truth is that the gate is impossible.
        """
        assert self.collective is not None
        # Defensive on purpose: `collective` is duck-typed (tests and embedders pass stubs), and a
        # helper whose only job is to emit a warning must never be the thing that breaks the run.
        models = getattr(self.collective, "transfer_models", None)
        if not models:
            return
        n = len(models)
        ceiling = best_possible_wilson(n, k)
        if ceiling < self.min_transfer:
            _log.warning(
                "accept_mode='wilson' can never accept: a perfect %d/%d scores %.3f (best of k=%d), "
                "below min_transfer=%.2f. Lower min_transfer, enlarge the panel, or use 'point'.",
                n, n, ceiling, k, self.min_transfer,
            )

    def _duplicata(self, candidato: LearnedSkill) -> str | None:
        """O nome do cartao ja' guardado que diz a mesma coisa, ou None.

        So' compara com cartoes do MESMO `kind`: um anti-padrao que descreve o mesmo assunto de um
        padrao nao e' duplicata dele — um diz "faca assim" e o outro "nao faca assim", e colapsar os
        dois apagaria metade do par.
        """
        alvo = _assinatura(candidato)
        if len(alvo) < 4:
            return None  # assinatura curta demais para afirmar semelhanca de nada
        for nome in self.store.names():
            outro = self.store.get(nome)
            if outro is None or getattr(outro, "kind", "") != getattr(candidato, "kind", ""):
                continue
            if _semelhanca(alvo, _assinatura(outro)) >= self.dedupe_at:
                return str(nome)
        return None

    def _mark_and_store(self, skill: LearnedSkill, *, tainted: bool) -> LearnedSkill:
        """Store a skill with anti-poisoning provenance (Zombie Agents defense).

        A skill distilled during a run that consumed untrusted content is marked
        ``tainted`` and held ``pending`` — it never enters retrieval until a human
        approves it (`chimera skills-approve`). Clean runs store active as before.
        """
        if tainted:
            skill.provenance = "tainted"
            skill.status = "pending"
            _log.debug("skill %s held PENDING (tainted-run provenance)", skill.name)
            if self.audit is not None:
                self.audit.record(
                    "taint_provenance",
                    {"artifact": "skill", "name": skill.name, "action": "held_pending"},
                )
        elif self.provisional:
            skill.status = "provisional"
            _log.debug("skill %s born PROVISIONAL (on measured probation)", skill.name)
        self.store.add(skill)
        return skill

    def _clears_holdout(self, candidate: LearnedSkill, task: str) -> bool:
        """False only when the holdout RAN and the candidate failed it.

        An unmeasured holdout does not reject — there is nothing to reject on — but it does not pass
        silently either: the skill is stored with its status recorded so "checked and cleared" and
        "never checked" stay different facts. `chimera/eval/transfer.py` set the same rule for the
        promotion path: an honest "promoted without a transfer check", never a silent pass.
        """
        if self.holdout is None:
            return True
        verdict = self.holdout.evaluate(candidate, minted_from=_task_id(task))
        if not verdict.measured:
            _log.info("auto-skill %s stored WITHOUT a holdout check: %s",
                      candidate.name, verdict.reason)
            self._record_holdout(candidate, verdict, kept=True)
            return True
        if not self.holdout.accepts(verdict):
            _log.info("discarded auto-skill %s: %s", candidate.name, verdict.summary())
            self._record_holdout(candidate, verdict, kept=False)
            return False
        self._record_holdout(candidate, verdict, kept=True)
        return True

    def _record_dedupe(self, novo: str, igual: str) -> None:
        """Uma deduplicacao silenciosa e' a mesma classe de silencio que este projeto passa o dia
        consertando: sem esta linha, "nao aprendeu nada" e "aprendeu e foi descartado por ja' saber"
        sao o mesmo nada no log."""
        if self.audit is None:
            return
        self.audit.record("skill_dedupe", {"discarded": novo, "same_as": igual})

    def _record_holdout(
        self, candidate: LearnedSkill, verdict: HoldoutVerdict, *, kept: bool
    ) -> None:
        """Write the verdict to the audit log, kept or not.

        The REJECTED ones are the half worth keeping: a gate whose rejection rate is zero is a gate
        that supports nothing built on top of it, and there is no way to notice that from the skills
        that survived it.
        """
        if self.audit is None:
            return
        self.audit.record(
            "skill_holdout",
            {
                "name": candidate.name,
                "kept": kept,
                "measured": verdict.measured,
                "passed": verdict.passed,
                "total": verdict.total,
                "excluded": verdict.excluded,
                "errors": len(verdict.errors),
            },
        )

    def maybe_evolve(
        self, task: str, solution: str, prior_successes: int, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Return the kept skill, or None if not recurring / rejected / untested."""
        if prior_successes < self.min_recurrences:
            return None  # not recurring enough yet
        if self.collective is not None:
            return self._evolve_collective(task, solution, tainted=tainted)
        return self._evolve_single(task, solution, tainted=tainted)

    def maybe_evolve_failure(
        self, task: str, detail: str, prior_failures: int, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Distill a RECURRING failure into an advisory anti-pattern card.

        Gated on recurrence (so a one-off failure doesn't spawn a card) and on the
        governance validator. There is no executable smoke test — an anti-pattern card is
        advisory (injected into reasoning, never run), so a bad card can only mislead, not
        act, and the verify-or-revert loop still decides success.
        """
        if prior_failures < self.min_recurrences:
            return None
        card = self.evolver.propose_failure_card(task, detail)
        if card is None:
            return None
        if card.name in self.store:
            _log.debug("anti-pattern card %s already exists; skipping", card.name)
            return None
        if self.validator is not None and not self.validator.validate(card.to_dict()).accepted:
            _log.debug("rejected anti-pattern card %s (failed validation)", card.name)
            return None
        self._mark_and_store(card, tainted=tainted)
        _log.debug("kept anti-pattern card %s", card.name)
        return card

    def maybe_distill_correction(
        self, task: str, failed: str, passed: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Distill a VERIFIED failed→passed correction into an anti-pattern card (M15-B4).

        Unlike ``maybe_evolve_failure`` (heuristic, recurrence-gated), this fires on a single
        transition because it is grounded in a *verified* fix — the eval turned fail into pass, so
        the diff between the two attempts is a real correction, not a guess. Gated on the governance
        validator; a card distilled during a tainted run is held pending like any other artifact.
        """
        card = self.evolver.distill_correction(task, failed, passed)
        if card is None:
            return None
        if card.name in self.store:
            _log.debug("correction card %s already exists; skipping", card.name)
            return None
        if self.validator is not None and not self.validator.validate(card.to_dict()).accepted:
            _log.debug("rejected correction card %s (failed validation)", card.name)
            return None
        self._mark_and_store(card, tainted=tainted)
        _log.debug("kept correction card %s", card.name)
        return card

    def _evolve_single(
        self, task: str, solution: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        candidate = self.evolver.propose(task, solution)
        if candidate is None:
            return None
        if candidate.name in self.store:
            _log.debug("auto-skill %s already exists; skipping", candidate.name)
            return None
        if (igual := self._duplicata(candidate)) is not None:
            _log.info("auto-skill %s descartada: diz o mesmo que %s", candidate.name, igual)
            self._record_dedupe(candidate.name, igual)
            return None

        # Gate 1 — governance: reject an unsafe proposal before it ever runs.
        if self.validator is not None and not self.validator.validate(candidate.to_dict()).accepted:
            _log.debug("rejected auto-skill %s (failed validation)", candidate.name)
            return None

        # Gate 2 — executable smoke test: the skill must run and produce output.
        test_input = {field: task for field in _placeholders(candidate.prompt_template)}
        if not self.evolver.test_skill(candidate, test_input, lambda out: bool(out.strip())):
            _log.debug("discarded auto-skill %s (failed smoke test)", candidate.name)
            return None

        # Gate 3 — black-box holdout: does it work on a task it was not minted from?
        if not self._clears_holdout(candidate, task):
            return None

        self._mark_and_store(candidate, tainted=tainted)
        _log.debug("kept auto-skill %s", candidate.name)
        return candidate

    def _evolve_collective(
        self, task: str, solution: str, *, tainted: bool = False
    ) -> LearnedSkill | None:
        """Propose across the fusion panel; keep the most transferable validated skill.

        Cross-model transferability is the executable gate here — it subsumes the
        single-model smoke test, since the skill must run and produce output on the
        panel models rather than on just one.
        """
        assert self.collective is not None
        best: LearnedSkill | None = None
        best_score = -1.0
        best_frac = 0.0
        # Materialise the panel's proposals so the selection size is known: the winner is the best
        # of k, and a bound taken on a winner has to pay for that choice (see wilson_lower_best_of).
        candidates = list(self.collective.propose_collective(task, solution))
        k = len(candidates)
        if self.accept_mode == "wilson":
            self._warn_if_gate_unsatisfiable(k)
        for candidate in candidates:
            if candidate.name in self.store:
                continue
            if self.validator is not None and not self.validator.validate(candidate.to_dict()).accepted:
                continue
            test_input = {field: task for field in _placeholders(candidate.prompt_template)}
            passed, n = self.collective.transfer_counts(
                candidate, test_input, lambda out: bool(out.strip())
            )
            frac = passed / n if n else 0.0
            # "wilson" gates on the lower confidence bound, corrected for having picked the best of
            # k, so neither a 2/3 fluke nor the winner's curse clears the threshold; "point" is the
            # raw fraction and pays neither cost.
            score = wilson_lower_best_of(passed, n, k) if self.accept_mode == "wilson" else frac
            _log.debug(
                "collective candidate %s: %d/%d (frac=%.2f gate=%.2f mode=%s)",
                candidate.name, passed, n, frac, score, self.accept_mode,
            )
            if score > best_score:
                best, best_score, best_frac = candidate, score, frac
        if best is not None and (igual := self._duplicata(best)) is not None:
            _log.info("auto-skill coletiva %s descartada: diz o mesmo que %s", best.name, igual)
            self._record_dedupe(best.name, igual)
            return None
        if best is None or best_score < self.min_transfer:
            _log.debug("no transferable auto-skill kept (best gate=%.2f)", best_score)
            return None
        # Gate 3 — black-box holdout. It has to be here as well as on the single path, and for a
        # sharper reason: the score above is the best of k candidates, so the winner's advantage is
        # partly the winner's curse. Asking it about a task it has never seen is the one question
        # that picking the best of k cannot flatter.
        if not self._clears_holdout(best, task):
            return None
        self._mark_and_store(best, tainted=tainted)
        _log.debug("kept collective auto-skill %s (frac=%.2f gate=%.2f)", best.name, best_frac, best_score)
        return best
