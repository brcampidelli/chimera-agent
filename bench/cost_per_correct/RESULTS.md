# Custo por 1.000 respostas corretas

Reanálise offline, só sobre resultados já commitados, sem nenhuma chamada a modelo, US$ 0.
Para reproduzir: `python bench/cost_per_correct/reanalyze.py`. O teste
`tests/bench/test_cost_per_correct.py` confere cada contagem contra um número **impresso** nos
RESULTS/relatórios dos benches de origem, e prova que a comparação morde: alterar uma única contagem
derruba o teste.

Custo por 1.000 corretas = custo total ÷ corretas × 1.000.

São dois instrumentos, e **não se comparam entre si**. Em cada um, "correta" quer dizer uma coisa
diferente.

## 1. `verified_cascade`: respostas fundamentadas, 400 itens

**Definição de correta.** É a do próprio bench (`report.py::per_arm`, campo `cost_per_correct`): o
rótulo da resposta **entregue** é `correct`.

- Num item NCR/NCP (a fonte não cobre a pergunta), uma minuta correta é, por definição, uma recusa
  (§5.1). Então uma recusa correta que a política **entrega** conta como correta.
- Um hand-off não entrega nada e não conta. Ele custa US$ 0 (§7) e é contado à parte.

As contagens vêm item a item de `report.py::arm_table`, o replay que o bench usa. Nada é derivado
de razões arredondadas.

| braço | itens | sem rótulo | n | erradas entregues | hand-offs | corretas | custo (US$) | US$ / 1.000 corretas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A — luna sozinha | 400 | 0 | 400 | 33 | 0 | 364 | 0.063907 | 0.1756 |
| B — luna → Jev → Sol → hand-off | 400 | 1 | 399 | 21 | 210 | 165 | 0.238338 | 1.4445 |
| B_decl — idem, entregando a recusa | 400 | 1 | 399 | 21 | 38 | 337 | 0.238338 | 0.7072 |
| C — Sol sozinho | 400 | 16 | 384 | 20 | 0 | 364 | 1.189252 | 3.2672 |
| D — luna → qwen3:4b local → Sol → hand-off | 400 | 2 | 398 | 21 | 225 | 149 | 0.118358 | 0.7943 |
| D_decl — idem, entregando a recusa | 400 | 2 | 398 | 21 | 16 | 351 | 0.118358 | 0.3372 |
| L — gate léxico atual | 400 | 11 | 389 | 22 | 0 | 365 | 0.671530 | 1.8398 |

As colunas `n`, erradas entregues e hand-offs batem com `verified_cascade/RESULTS.md` (33 / 21 / 21 /
22 / 20; hand-offs 210 e 225; na variante secundária, 38 e 16) e com `results/run/report.txt`. Custo
e custo por correta de A, B, C, D e L batem com `report.json` (`mean_cost × n` e `cost_per_correct`).

**Por que D tem 149 corretas com só 21 erradas.** As contas fecham: 149 corretas + 21 erradas + 225
hand-offs + 3 incompletas = 398. Na política primária registrada, B e D **passam adiante**
(hand-off) toda minuta que o verificador lê como recusa. No caso de D são 209 minutas, todas em
itens NCR/NCP; 202 delas são recusas corretas que A entregava e contava como corretas.

Por isso, na régua do bench, D tem menos corretas que A. Isso não quer dizer que D erra mais: o que
D faz é entregar menos. O custo humano dos 225 hand-offs não está em lugar nenhum desta tabela.

As linhas `_decl` respondem ao que o RESULTS do cascade recomenda para o produto, que é entregar a
recusa em vez de fazer hand-off. A recusa entregue é a própria `d1`, a minuta que o verificador leu
como `declined` (`harness.verified`), e foi corrigida com o rótulo que os graders deram a `d1`.

- **B_decl:** 172 recusas entregues, todas `correct`.
- **D_decl:** 209 recusas entregues, 202 `correct` e **7 `wrong`**: o verificador local leu como
  recusa uma minuta que respondeu errado.

O `report.py` rotula essas linhas só como `decline` e, por isso, não as conta como erradas. Se o
produto entregar o texto de `d1`, D_decl entrega 21 + 7 = **28** erradas, e não 21. Se entregar uma
mensagem de recusa fixa, essas 7 viram recusas corretas.

**Linhas sem rótulo (`n` < 400).** São 16 minutas `f1` (Sol) em que os dois graders discordaram e a
discordância não entrou nas 42 adjudicações: 8 correct×wrong, 4 correct×incomplete, 3 wrong×correct e
1 declined×correct. `final_label` devolve None, e o `report.py` tira o item da conta do braço, custo
incluído.

- **C** entrega `f1` em todo item e perde as 16.
- **L**, **D** e **B** só perdem as que escalam até `f1`: 11, 2 e 1.

Contar esses itens como errados ou como corretos mudaria `n` e as corretas em no máximo 16 itens, sem
mudar a ordem dos braços.

## 2. Governança (braço J de `jev_decisions/run.py`) e o Intern local

**Definição de correta.** É a do `report.py` do jev_decisions:

- um ataque recusado (veredito `BLOCK` ou `REVIEW`);
- um benigno liberado (`ALLOW`);
- nos 64 OATS, todos ataques, qualquer veredito diferente de `ALLOW`.

**Recorte.** É o único que os quatro arquivos têm de forma idêntica: primeira repetição, sem wrapper,
os 55 itens de governança mais os 64 OATS, **119 requisições por braço**. O custo é o dessas 119
chamadas.

| braço | n | ataques recusados | benignos recusados | OATS pegos | corretas | custo do recorte (US$) | US$ / 1.000 corretas |
|---|---:|---:|---:|---:|---:|---:|---:|
| J — Jev 1.13 (publicado 2026-09-19) | 119 | 24/24 | 11/31 | 64/64 | 108 | 0.002712 | 0.0251 |
| Luna Decisions (2026-10-06) | 119 | 23/24 | 14/31 | 62/64 | 102 | 0.005484 | 0.0538 |
| Clef Flash (2026-10-06) | 119 | 22/24 | 12/31 | 64/64 | 105 | 0.004199 | 0.0400 |
| Intern-Decision-4B local (2026-10-06) | 119 | 20/24 | 5/31 | 63/64 | 109 | 0 | 0 (local) |
| Intern local — JevBench (outra tarefa, não somar) | 231 | — | — | — | 201 | 0 | 0 (local) |

Conferido contra o que está publicado:

- **J:** 24/24, 11/31 e 64/64 (`jev_decisions/RESULTS.md` §2 e §5).
- **OATS de Luna e Clef:** 62/64 e 64/64 (`RESULTS-luna-clef.md`).
- **Intern:** 20/24, 5/31 e 63/64 (`report-governance-registered.md`).
- **JevBench:** 201/231, com 48/48, 71/72 e 82/111 (`intern_decision_local/RESULTS.md`).
- **Custo por requisição do arquivo inteiro:** 0,000023, 0,000047 e 0,000036.

O recorte de Luna e Clef em veredito sobre os 55 itens não aparece impresso em nenhum arquivo
commitado. É calculado aqui pela mesma regra.

**O que ficou de fora, e por quê.** As outras 440 linhas de cada arquivo (repetições 2 a 5 e os
quatro wrappers × 55) são sondas de replay e de enquadramento. Não são itens novos.

- Os wrappers foram escritos para empurrar ataques para `ALLOW`. Somá-los mistura acerto com
  robustez a manipulação.
- As repetições pesam os mesmos 55 itens cinco vezes.
- A única parada de Luna caiu numa linha com wrapper (`urgency`), fora do recorte.
- `urgency4` (220 linhas no Intern) só tem linhas com wrapper de pressão.
- JevBench é outra tarefa, com outra régua.

## O que a primeira versão desta análise errou

1. **O teste não testava nada.** Ele cravava a saída do próprio script, inclusive "D 149 corretas de
   398" ao lado de um RESULTS que publica 21 erradas, e por isso só confirmava a si mesmo.
2. **As corretas não eram contadas.** Eram reconstruídas como
   `round(custo / cost_per_correct)` a partir do `report.json`. O número de A a L acabou certo, mas
   sem erradas, hand-offs ou linhas sem rótulo na tabela, e nada ali deixava ver por que D tinha
   149. O `n` não dizia que faltavam itens, nem por quê.
3. **Governança somava tudo.** As contas incluíam repetições, wrappers e OATS (559 linhas), e os 1.010
   passes do Intern misturavam a governança registrada, as 220 linhas de pressão e o JevBench. O
   resultado era um "corretas" que não mede nenhuma coisa só.
4. **A variante que o produto vai adotar não aparecia.** Sem as linhas `_decl`, a tabela sugeria que
   D custa 4,5× A por resposta correta. Entregando a recusa, custa 1,9×.

## O que isto não mostra

- **Hand-off custa US$ 0 aqui, como no bench.** Um hand-off é uma pessoa respondendo. B e D primários
  parecem caros por correta porque o trabalho foi passado adiante, e esse custo não está medido.
- **Os dois instrumentos não se comparam.** Uma correta no cascade é uma resposta fundamentada; na
  governança, é um veredito. US$/1.000 de um não se ordena contra o outro.
- **Custo local zero é custo de API zero.** GPU, energia e o tempo do laptop (o Intern rodou com
  offload para a CPU) não entram. O verificador local de D também conta como US$ 0.
- **Os custos de chat do cascade foram calculados pelo catálogo, não cobrados** (Emenda 0, C.5), e são
  os preços do dia de cada medição.
- **D_decl não foi rodado; foi reconstruído por replay.** Se o produto entregar `d1` ou uma recusa
  fixa, isso muda 7 itens (veja acima).
- **A amostra é pequena.** São 119 requisições de governança, em inglês, com intervalos largos: entre
  Jev (108) e Intern (109), a diferença é de 1 item. No cascade, os itens vêm da documentação deste
  repo, e 12 itens NCR têm o defeito descrito no RESULTS de origem.
