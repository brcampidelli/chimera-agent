---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmarki — dowód na wzmocnienie słabego modelu

Teza Chimery brzmi, że struktura sprawia, iż **słaby/tani** model bije ponad swoją wagę. Uczciwym
sposobem, by to pokazać, jest kontrolowany test A/B na standardowym benchmarku: zamrozić podzbiór
zadań i model, uczynić rusztowanie (scaffolding) **jedyną** zmienną, i raportować deltę z
przedziałem ufności — nie gołe "poprawiło się". (Niezależne badania pokazują, że ten sam model
waha się o ~7pkt wyłącznie z powodu rusztowania, więc niezakwalifikowany wynik nic nie mówi o
*twoim* wkładzie.)

## Eksperyment

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — zadanie Docker + instrukcja +
testy weryfikacyjne, oceniane pass/fail przez te testy, napędzane przez agnostyczny wobec agenta
harness **Harbor**.

- **Ramię A (baseline):** jeden darmowy model w neutralnym rusztowaniu Harbora — "sam słaby
  model".
- **Ramię B (treatment):** **ten sam** model, **te same** identyfikatory zadań, napędzane przez
  Chimerę.
- **Metryka:** pass@1. **Nagłówek:** Δ = rate(B) − rate(A), z 95% przedziałem ufności.
- **Strażnicy uczciwości:** przypiąć podzbiór identyfikatorów zadań (opublikować go), wykonać
  ≥3 seedy, opublikować wszystkie transkrypty, i dodać wiersz z modelem frontier tylko jako
  *punkt odniesienia sufitu* — nigdy jako porównanie.

### Wynik — i wypadł przeciwko nam

Ta strona kończyła sekcję, wskazując liczbę, która *dowiodłaby* tezy: „sam darmowy model = X%,
darmowy model + Chimera = Y%, Y ≫ X". Eksperyment został od tego czasu przeprowadzony, a Y wyszło
**poniżej** X. Na prerejestrowanym wycinku N=40 z tym samym modelem w obu ramionach
(`deepseek-chat-v3.1`): **7,5% → 2,5%**, **sparowana Δ −5,0 pp, 95% CI [−5,0%, +1,6%] —
nieistotne statystycznie**. Rusztowanie nie podniosło modelu, który już był kompetentny; oba
ramiona leżą na podłodze zdominowanej przez wariancję. Pełny raport, wraz z prerejestracją spisaną
przed przebiegiem: [`bench/terminal_bench/RESULTS.md`](../../../bench/terminal_bench/RESULTS.md).

Zdanie obiecujące `Y ≫ X` przetrwało przebieg, który je obalił — na tej stronie i w dziewięciu
tłumaczeniach. Zostaje tu odnotowane, a nie po cichu usunięte, bo projekt, którego jedynym realnym
aktywem jest uczciwy pomiar, nie może sobie pozwolić na stronę przewidującą odwrotność własnego
wyniku.

## Uruchamianie

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera podłącza się jako agent treatment przez `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` buduje `BaseAgent` Harbora, który uruchamia `chimera solve` z
flagami rusztowania). Skieruj Harbora na przypięty podzbiór i darmowy model dla każdego ramienia;
zajrzyj do [dokumentacji Harbora](https://www.tbench.ai/) po dokładne wywołanie `harbor run` i
`--agent-import-path`.

## SWE-bench Verified (druga tablica wyników) — **uruchomione czterokrotnie**

Terminal-Bench dowodzi tezy na zadaniach CLI; SWE-bench dowodzi jej na prawdziwych poprawkach
błędów z GitHuba — mając repozytorium przy commicie bazowym oraz issue, agent musi wyprodukować
patch, który sprawia, że testy `FAIL_TO_PASS` danej instancji przechodzą, przy zachowaniu
zielonych `PASS_TO_PASS`. "Verified" to podzbiór zwalidowany przez ludzi.

### Wyniki

Cztery wcześniej zarejestrowane uruchomienia na wycinkach
`django/django` (najłatwiejsza warstwa trudności), `deepseek-chat-v3.1`, pass@1, ocenione
**wyłącznie** przez oficjalny harness `swebench` 4.1.0 w Dockerze. Pełne opracowanie:
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| run | baseline | + Chimera | paired Δ | 95% CI | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | not significant |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | not significant |
| **3 (replikacja)** | 34.1% (14/41) | **43.9% (18/41)** | **+9.8%** | [−3.5%, +16.7%] | nieistotne |
| **zbiorczo (drugorzędne)** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **istotne** |
| 4 (atrybucja) | 34.1% | *samo rusztowanie* 39.0% | +4.9% | [−7.6%, +14.2%] | nieistotne |

Uruchomienie 1 to **dokładne zero** i jest opublikowane bez zmian. Uruchomienie 2 naprawiło dwie
wady, które były *nasze* — rusztowanie działało bez swojego najsilniejszego mechanizmu, a 8 kroków
wywołań narzędzi to za mało, by nawigować po 250 MB repozytorium — i wyszło z wynikiem **3
instancje wygrane, 0 przegranych**. Ta para jest odkryciem: rusztowanie jest warte *nic*, gdy
agentowi brakuje kroków, i *trzy instancje*, gdy ich nie brakuje, a wygrywa **lepszą** edycją (69%
vs 57% precyzji, gdy edytuje), a nie edytowaniem więcej.

> ⚠️ **Żaden z tych wyników nie jest wynikiem SWE-bench Verified.** Wycinek jest celowo łatwy i
> jednorepozytoryjny, wybrany tak, by test A/B miał miejsce do pomiaru; prawdziwy wynik Verified
> potrzebuje pełnych 500. A delta **nie jest istotna statystycznie** — przy 8 parach, w których
> obie strony zawodzą, n=19 zostawia tylko trzy informatywne pary.

Uruchomienie 2 przynosi też **retrakcję**: mechanizm, który wytropiliśmy dla pustych patchy z
uruchomienia 1, był błędny (naprawą był budżet kroków, nie diff-gate, który obwiniliśmy),
skorygowaną tak samo widocznie, jak było to zgłoszone.

To 3–0 na trzech informatywnych parach ma dokładnie kształt, jaki daje szczęśliwa próbka, a
prerejestracja dawała temu **jedną szansę na trzy, że to tylko tyle**. Uruchomienie 3 powtórzyło
więc wszystko na **41 instancjach, których wyników nigdy nie widzieliśmy**, nie zmieniając nic
innego. Efekt **pojawił się ponownie**: +9,8%, wewnątrz zarejestrowanego pasma od +5 do +20, na
wycinku, który okazał się *trudniejszy* niż ten z uruchomienia 2 (baseline 34,1% wobec 42,1%).
Uruchomienie 4 rozdzieliło potem rusztowanie i diff-gate na tych samych 41: **po +4,9%**, a
mechanizmem jest precyzja, która rośnie 50% → 59% → 67%, podczas gdy odsetek łatek nie drgnie. Żadne
pojedyncze uruchomienie nie jest istotne; zbiorcze n=60 jest — i zostało prerejestrowane jako
**drugorzędne** właśnie dlatego, że miesza dane widziane z niewidzianymi.

### Adapter

Adapter (`chimera.eval.swe_bench`) jest uczciwy co do swojej granicy: czyste części — wywołanie
`chimera solve` na instancję (ramię treatment) i parsowanie oficjalnego raportu ewaluacji — żyją
tutaj i są testowane jednostkowo; zbiór danych i harness ewaluacji Dockera są **opcjonalne i nie
są dołączone**, a werdykt pass/fail pochodzi z własnych testów SWE-bench, nigdy z
samo-raportowania.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Oba raporty są rzutowane na wspólną listę instancji (brakujący identyfikator liczy się jako
nierozwiązany), więc oba ramiona są zawsze porównywane na identycznych instancjach — a następnie
stosowany jest ten sam werdykt Newcombe-CI.

## Ocenianie A/B (bez potrzeby benchmarku)

Gdy każde ramię wyprodukuje pass/fail per zadanie, statystyka to jedna komenda — nie potrzeba do
tego **niczego dodatkowego**, więc silnik uczciwego raportowania jest zawsze dostępny:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Każdy plik to lista JSON wartości logicznych (lub `{task_id: bool}`) nad **tymi samymi**
identyfikatorami zadań. Wynik: wskaźnik zdawalności każdego ramienia ograniczony metodą Wilsona,
delta, jej 95% przedział ufności Newcombe, oraz czy różnica jest **istotna statystycznie**
(przedział ufności wyklucza zero). Jeśli nie jest istotna, jest to raportowane wprost — potrzeba
większego podzbioru / więcej seedów, albo funkcja naprawdę nie porusza liczby.

To samo `bench-compare` jest miarą dla każdej późniejszej funkcji: każdy dodatek M14 musi
pokazać, że porusza Δ na identycznym podzbiorze, albo zostaje wycięty.

## Uczciwa pułapka (czego unikać)

- **Kontaminacja** — publiczny SWE-bench ma udokumentowany przeciek rozwiązań; preferuj zbiory
  odporne na kontaminację i raportuj to zastrzeżenie.
- **Konfundowanie rusztowania** — nigdy nie raportuj gołego "osiągnęliśmy X%"; tylko delta A/B
  izoluje wkład Chimery.
- **Zły baseline / wybieranie wisienek** — porównuj słaby+Chimera z *tym samym słabym modelem
  samym w sobie*, na *identycznych* identyfikatorach zadań, z seedami i pełnymi logami. Model
  frontier jest sufitem, nie rywalem.
