"""Read `rows.json` and answer the question `PREREGISTRATION.md` fixed, in the order it fixed it.

    python bench/edit_format_by_model/analyse.py

The gates come first and the aggregate second, because a cell where an arm failed carries the cost of
failing rather than the cost of the format, and a cell where the denied tool was used did not run the
condition its label claims.

The question is an INTERACTION. A format cheaper for every model is a better default, not a reason
to tailor — so the verdict reads the SIGN of the within-family difference across families, never a
pooled median that would average the two answers into neither.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

HERE = Path(__file__).resolve().parent
MARGIN = 0.20  # the pre-registered floor: 20% of the family's own median


def main() -> int:
    rows = json.loads((HERE / "rows.json").read_text(encoding="utf-8"))
    print(f"  {len(rows)} linhas\n")

    # --- gates, before any aggregate -------------------------------------------------------------
    failed = [r for r in rows if not r["passed"]]
    # NOT a void. `tool_names` is appended BEFORE the tool runs (`agent.py:729`, against `_run_tool`
    # at :733), and a denied tool is absent from the registry, so `_run_tool` returns
    # "error: unknown tool" and nothing executes. A denied name in this list is therefore the model
    # ASKING and being refused — the condition working, plus the model discovering it. Voiding those
    # pairs would discard the runs where the manipulation bit hardest.
    asked_denied = [r for r in rows if r["used_denied"]]
    idle = [r for r in rows if not r["used_expected"]]
    # A run killed at the timeout wrote no receipt, so its cost is recorded as zero — and `_verdict`
    # still runs pytest over whatever the process had already written, so a timeout can score
    # `passed=True` at zero tokens. That is a free success in a comparison whose entire outcome is
    # cost. Voided on the measurement, not on the verdict: a run whose price was never recorded
    # cannot be in a table of prices.
    unmeasured = [r for r in rows if r["exit"] == 124 or r["completion_tokens"] == 0]
    print("  === portoes ===")
    print(f"    reprovou o verificador : {len(failed)}")
    for r in failed[:8]:
        print(f"       {r['model'].split('/')[-1][:24]:24} {r['arm']} {r['task']}")
    print(f"    sem custo registrado   : {len(unmeasured)}   (timeout / recibo ausente)")
    for r in unmeasured[:8]:
        print(f"       {r['model'].split('/')[-1][:24]:24} {r['arm']} {r['task']} "
              f"exit={r['exit']} passou={r['passed']}")
    print(f"    nao usou o esperado    : {len(idle)}   (o braco nao rodou a sua condicao)")
    print(f"    pediu o que foi negado : {len(asked_denied)}   (recusado; informativo, nao anula)")

    void = {(r["model"], r["arm"], r["task"]) for r in (*failed, *unmeasured, *idle)}
    # Void by PAIR: a comparison needs both arms of the same task on the same model.
    void_pairs = {(m, t) for (m, _a, t) in void}
    usable = [r for r in rows if (r["model"], r["task"]) not in void_pairs]
    print(f"\n    pares descartados: {len(void_pairs)}   linhas usaveis: {len(usable)}")

    # --- the interaction --------------------------------------------------------------------------
    by: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in usable:
        by[r["model"]][r["task"]][r["arm"]] = r["completion_tokens"]

    print("\n  === tokens de saida, por familia (mediana sobre tarefas pareadas) ===")
    print(f"  {'modelo':30} {'n':>3} {'P (patch)':>10} {'W (inteiro)':>12} {'W-P':>8} {'% da mediana':>13}")
    verdicts: dict[str, float] = {}
    spans: dict[str, tuple[float, float]] = {}
    random.seed(7)  # the interval must be the same number every time it is quoted
    for model, tasks in by.items():
        pairs = [(v["P"], v["W"]) for v in tasks.values() if "P" in v and "W" in v]
        if not pairs:
            continue
        p_med = statistics.median(p for p, _ in pairs)
        w_med = statistics.median(w for _, w in pairs)
        # The per-task difference, then its median — not the difference of medians. The tasks are the
        # paired unit, and differencing the summaries throws the pairing away.
        deltas = [w - p for p, w in pairs]
        delta = statistics.median(deltas)
        base = statistics.median([*(p for p, _ in pairs), *(w for _, w in pairs)])
        share = delta / base if base else 0.0
        verdicts[model] = share
        # A median alone cannot say whether it differs from zero, and with one repeat per cell that
        # is the question. Bootstrapped over the paired per-task deltas — added because the medians
        # came out at 10-12% with opposite signs, which reads as a near-miss until the interval is
        # printed beside it. It makes a reject stronger; it could not have turned one into an adopt.
        boots = sorted(
            statistics.median(random.choices(deltas, k=len(deltas))) for _ in range(4000)
        )
        lo, hi = boots[100], boots[3900]
        spans[model] = (lo, hi)
        print(
            f"  {model.split('/')[-1][:30]:30} {len(pairs):3} {p_med:10.0f} {w_med:12.0f} "
            f"{delta:+8.0f} {share * 100:+12.1f}%   IC95 [{lo:+.0f}, {hi:+.0f}]"
            f"{'' if lo * hi > 0 else '  INCLUI ZERO'}"
        )

    print("\n  === o veredito, pela regra fixada antes ===")
    big = {m: s for m, s in verdicts.items() if abs(s) >= MARGIN}
    signs = {m: (1 if s > 0 else -1) for m, s in big.items()}
    disagree = len(set(signs.values())) > 1
    print(f"    familias com |diferenca| >= {MARGIN:.0%}: {len(big)} de {len(verdicts)}")
    for m, s in verdicts.items():
        cheaper = "P (patch)" if s > 0 else "W (inteiro)"
        forte = "sim" if abs(s) >= MARGIN else "nao"
        print(f"       {m.split('/')[-1][:30]:30} mais barato: {cheaper:12} margem>=20%: {forte}")
    crosses = [m for m, (lo, hi) in spans.items() if lo * hi <= 0]
    if crosses:
        print(f"    familias cujo IC95 inclui zero: {len(crosses)} de {len(spans)}")
        print("       nao ha efeito mensuravel para uma inversao de sinal descrever")
        print("\n    >>> REJEITAR: o efeito nao se distingue de zero em nenhuma familia.")
    elif disagree and len(big) >= 2:
        print("\n    >>> CONSTRUIR: o sinal diverge entre familias, com margem em pelo menos duas.")
    elif len(set(1 if s > 0 else -1 for s in verdicts.values())) == 1:
        print("\n    >>> REJEITAR: o mesmo formato e' mais barato em todas as familias.")
        print("        Isso e' um achado sobre o PADRAO, nao sobre ajuste por modelo.")
    else:
        print("\n    >>> REJEITAR: as diferencas nao alcancam a margem de 20% em duas familias.")

    # --- secondary, reported and never used to decide ------------------------------------------------
    print("\n  === secundario (reportado, nunca decide) ===")
    for model, tasks in by.items():
        pairs = [(v["P"], v["W"]) for v in tasks.values() if "P" in v and "W" in v]
        if not pairs:
            continue
        rs = [r for r in usable if r["model"] == model]
        for arm in ("P", "W"):
            a = [r for r in rs if r["arm"] == arm]
            if not a:
                continue
            print(
                f"    {model.split('/')[-1][:26]:26} {arm}  "
                f"passou {sum(r['passed'] for r in a)}/{len(a)}  "
                f"edits mediana {statistics.median(r['edit_calls'] for r in a):.0f}  "
                f"usd {sum(r['usd'] for r in a):.4f}  "
                f"seg mediana {statistics.median(r['seconds'] for r in a):.0f}"
            )
    print(f"\n    custo total da grade: usd {sum(r['usd'] for r in rows):.4f}")

    # The verdict the ORIGINAL gate would have produced, printed beside the corrected one.
    #
    # The gate was corrected on a fact about the instrument — `tool_names` records requests, not
    # executions — which is verifiable in `agent.py` and independent of any outcome. But the
    # correction was made AFTER a partial figure had been printed by a dry run of this script, and
    # that is recorded rather than smoothed over. Showing both is what lets a reader decide whether
    # the correction moved the answer, instead of taking my word that it could not have.
    strict_void = {(r["model"], r["task"]) for r in (*failed, *asked_denied, *idle, *unmeasured)}
    strict = [r for r in rows if (r["model"], r["task"]) not in strict_void]
    print("\n  === sob a guarda ORIGINAL (que anulava tambem quem PEDIU o negado) ===")
    sby: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for r in strict:
        sby[r["model"]][r["task"]][r["arm"]] = r["completion_tokens"]
    for model, tasks in sby.items():
        pairs = [(v["P"], v["W"]) for v in tasks.values() if "P" in v and "W" in v]
        short = model.split("/")[-1][:30]
        if not pairs:
            print(f"    {short:30} sem pares usaveis")
            continue
        delta = statistics.median([w - p for p, w in pairs])
        base = statistics.median([*(p for p, _ in pairs), *(w for _, w in pairs)])
        pct = delta / base * 100 if base else 0.0
        print(f"    {short:30} n={len(pairs):2}  W-P={delta:+7.0f}  {pct:+6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
