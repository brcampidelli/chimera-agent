---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Rachunki fuzji — "selektywna fuzja z rachunkami"

Rdzeń rozumowania Chimery miesza **panel** modeli (panel → judge → syntetyzator). Fuzja kupuje
jakość, ale kosztuje więcej tokenów, więc uczciwe pytanie nigdy nie brzmi "czy fuzja jest dobra?",
tylko "**czy się tutaj opłaciła?**". Rachunki odpowiadają na to liczbami, nie deklaracją.

Każde uruchomienie fuzji może zostać wycenione w **rachunku** (receipt): ile kosztował każdy
doradca (członek panelu), sędzia (judge) i syntetyzator — każdy wg stawki *swojego własnego*
modelu — plus czy tryb selektywny skrócił obwód panelu. Zachowaj rachunki, a otrzymasz
publikowalną **krzywą koszt × jakość**.

## Wypróbuj

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` raportuje **wskaźnik fuzji** (jak często pełny panel faktycznie się
uruchomił vs. selektywne skrócenie obwodu), średni/łączny koszt po przebiegach o znanej cenie,
oraz — gdy rachunki niosą sygnał jakości pass/fail — wskaźnik zdawalności i **dolary na jedną
zdaną odpowiedź**.

## Reguły uczciwości (z konstrukcji)

- **Tokeny są mierzone; dolary są szacowane.** Liczby tokenów pochodzą od providera; kwota w
  dolarach jest liczona wg przybliżonej publicznej **ceny cennikowej**, więc rachunek jest
  estymatorem, nie rachunkiem do zapłaty.
- **Nieznany model → nieznany koszt, nigdy zero.** Jeśli którykolwiek etap uruchamia model bez
  ceny w rejestrze, suma rachunku wynosi `None` (`unknown`), więc brakująca cena nie może udawać
  "darmowej". Ceny są nadpisywalne w kodzie (`chimera.fusion.set_price`).
- **Atrybucja per doradca.** Koszt panelu jest rozbity *per model* (`receipt.advisor_costs`), więc
  widać, który doradca zarobił na swoje utrzymanie — to jest substancja stojąca za selektywną
  fuzją, nie slogan.

## Dlaczego to istnieje

Dziedzina przesunęła się w stronę routingu/kaskad (wydawaj więcej tylko wtedy, gdy stawka to
uzasadnia), i odeszła od stale włączonej fuzji. Rachunki są tym, co pozwala Chimerze fuzjonować
**selektywnie i to udowodnić** — krzywa koszt×jakość to dowód, publikowany łącznie z przebiegami,
w których fuzja *nie* pomogła.
