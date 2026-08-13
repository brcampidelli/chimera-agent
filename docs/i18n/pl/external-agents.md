---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Agenci zewnętrzni (ACP)

Chimera może przekazać turę kodowania agentowi, którego nie napisała — Claude Code, Gemini CLI albo
dowolnemu adapterowi mówiącemu [Agent Client Protocol](https://agentclientprotocol.com). Zapis
rozmowy, weryfikator, migawka i cofnięcie pozostają po stronie Chimery; pracę wykonuje ktoś inny.

## Dlaczego

Teza Chimery nigdy nie brzmiała, że jej pętla jest jedyną dobrą. Chodzi o zarządzanie *wokół* pętli:
rejestr skażenia, region zapisu, kopię przed turą, werdykt po niej, pokwitowanie mówiące, co
naprawdę się stało. To działa dla dowolnego wykonawcy. Odmowa sterowania wykonawcą, któremu już
ufasz, byłaby upieraniem się przy mniej interesującej połowie produktu.

## Co jest gwarantowane, a co nie

Przeczytaj tę część przed instalacją, bo to ona decyduje, czy ta funkcja jest dla Ciebie.

Agent ACP deklaruje, z których możliwości klienta skorzysta, a Chimera oferuje `fs/read_text_file`
i `fs/write_text_file`. **Oferowanie to nie egzekwowanie.** Agenci warci sterowania mają własne
narzędzia do plików i powłoki: Claude Code zapisuje przez Claude Agent SDK i nie ma obowiązku pytać
nas pierwszego.

Konkretnie:

| | Własna pętla Chimery | Agent zewnętrzny |
|---|---|---|
| Region zapisu odrzuca zapis poza nim | Zawsze | Tylko to, co idzie przez nas |
| Powłoka działa w skonfigurowanej piaskownicy | Zawsze | Agent uruchamia po swojemu |
| Rejestr skażenia uzbraja blokadę | Zawsze | Tylko narzędzia, które pośredniczymy |
| Migawka katalogu przed turą | Tak | **Tak** |
| Cofnięcie całej tury jednym kliknięciem | Tak | **Tak** |
| Każde udzielone zezwolenie w pokwitowaniu | — | **Tak** |

Trzy ostatnie wiersze to prawdziwa gwarancja i to właśnie obiecuje linia postawy na ekranie Kod, gdy
wybrany jest agent zewnętrzny. Przestaje mówić „edytuje w `/projekt`, nie uruchamia poleceń" — to
zdanie opisuje narzędzia należące do Chimery — a zamiast tego mówi, że wykonano kopię i że turę można
cofnąć. Ekran, który zachowałby mocniejsze zdanie, składałby obietnicę, której tura nie może
dotrzymać.

Chimera **odrzuca** też możliwość terminala z ACP. Terminal hostowany przez nas byłby drugą ścieżką
wykonania obok piaskownicy, bez żadnej z jej reguł.

## Konfiguracja

Dla agentów, których Chimera zna, nie ma nic do skonfigurowania:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, wymaga Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (tryb ACP jest u źródła eksperymentalny)
```

Następnie sprawdź, co ta maszyna naprawdę potrafi uruchomić:

```bash
chimera doctor
```

`external_agents` raportuje każdego z `available: true/false`, a gdy fałsz — linię, która to naprawia.
Dostępność jest ustalana na maszynie, gdzie działa sidecar — a w zapakowanej wersji desktopowej jest
to maszyna złożona przez CI, na którą nikt nie patrzył. Czyli: „powinno tam być" nie jest dowodem.

Aplikacja desktopowa pokazuje nad polem wiadomości wiersz **Kto wykonuje** z tym, co znalazł
`doctor`. Gdy nic uruchamialnego nie jest zainstalowane, wiersz w ogóle się nie pojawia; `doctor` to
miejsce na „jeszcze tego nie masz, a oto jak to zdobyć".

## Poświadczenia

Każdy proces potomny uruchamiany przez Chimerę dostaje środowisko pozbawione zmiennych `API_KEY` /
`TOKEN` / `SECRET`, żeby polecenie powłoki nie mogło wypisać klucza dostawcy. Agent ACP to program,
którego cała praca jednego wymaga, więc każdy agent deklaruje **po nazwie** potrzebne mu zmienne i
tylko one wracają:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Przekazanie całego środowiska byłoby łatwiejsze i wręczyłoby każdemu przyszłemu adapterowi wszystkie
klucze na maszynie.

## Własny adapter

Codex i inni docierają do ACP przez adaptery firm trzecich, których ten projekt nie uruchamiał.
Zamiast wypisywać niesprawdzone polecenie — co zamieniłoby „nie sprawdziliśmy" we „wspierane" —
wskaż Chimerze ten, który masz:

```jsonc
// POST /api/code/turn
{
  "message": "napraw failujący test",
  "provider": "custom",
  "provider_command": "npx -y jakis-adapter-acp --flag"
}
```

Polecenie jest dzielone w stylu powłoki i uruchamiane **bez** powłoki, więc zabłąkany potok jest
argumentem, a nie drugim poleceniem. W Windows argument zawierający składnię cmd.exe (`& | < > ^ %`),
który trafia do launchera `.cmd`, jest odrzucany zamiast escapowany: reguły cytowania różnią się
między launcherami, a błędne zgadnięcie uruchamia Twoją maszynę zamiast programu na niej.

## Jak to działa

- Jeden proces potomny na **rozmowę**, nie na turę. `session/prompt` to jedna wiadomość w kontekście,
  który trzyma agent; nowy proces za każdym razem czyniłby z każdej tury turę pierwszą.
- Maksymalnie cztery naraz, a nietknięty przez godzinę jest zamykany. Każdy to proces trzymający
  połączenie z modelem.
- Proces rodzi się we własnej grupie i jest zabijany jako drzewo — agent kodujący jest launcherem, a
  zabicie tylko procesu, który trzymamy, zostawiłoby działających pracowników i zablokowany katalog.
  Reaper w `atexit` obejmuje przypadek zamknięcia aplikacji w środku tury.
- Powiadomienia `session/update` agenta są tłumaczone na te same zdarzenia, które emituje pętla
  natywna, więc ekran nie potrzebuje drugiej implementacji. Fragmenty rozumowania są odrzucane, a nie
  wtapiane w odpowiedź; blok `diff` staje się ujednoliconą łatką, którą zapis już renderuje.
- Liczby, które ma pętla natywna, a których ta nie potrafi zgłosić — `steps`, `context_peak_tokens` —
  przychodzą jako `null`, nie `0`. Zero czytałoby się jako „nic nie zrobił".

## Ograniczenia

- Prośby o zezwolenie dostają `allow_once` i są **zapisywane w pokwitowaniu**. Blokowanie prośby,
  której agent wcale nie musiał składać, to teatr; uczciwa wersja to udzielić, zapisać i oprzeć się
  na migawce — która obejmuje też zapisy, które nigdy nie pytały.
- Fuzja, role, pamięć i mapa repozytorium należą do własnej pętli Chimery. Tura zewnętrzna raportuje
  `fused: false` i zero użycia pamięci, bo nic z tego się nie wydarzyło.
- Tryb ACP Gemini jest u źródła oznaczony jako eksperymentalny i jego zachowanie może się zmieniać
  między wydaniami.
