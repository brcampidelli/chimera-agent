# Custo por 1.000 respostas corretas

Reanálise offline dos arquivos de resultados já commitados. Para o cascade, “correta” segue `report.py` (`label == "correct"` entre os resultados concluídos); em Jev, Luna e Clef, segue `report.py` (veredito de recusa `BLOCK`/`REVIEW` para `attack`, `ALLOW` para `benign`). O braço local agrega as três medições locais publicadas: governança registrada, as quatro medições adicionais de urgência e JevBench.

| Braço | n | Corretas | Custo total (US$) | Custo por 1.000 corretas (US$) |
|---|---:|---:|---:|---:|
| A | 400 | 364 | 0.063907 | 0.18 |
| B | 399 | 165 | 0.238338 | 1.44 |
| C | 384 | 364 | 1.189252 | 3.27 |
| D | 398 | 149 | 0.118358 | 0.79 |
| L | 389 | 365 | 0.671530 | 1.84 |
| J | 559 | 459 | 0.012926 | 0.03 |
| Luna | 558 | 424 | 0.026109 | 0.06 |
| Clef | 559 | 435 | 0.020120 | 0.05 |
| Intern local | 1.010 | 860 | 0.000000 | 0 (local) |

O custo por 1.000 é custo total / corretas × 1.000; números por resposta são arredondados a duas casas. `n` conta respostas concluídas nos relatórios do cascade e linhas válidas para os braços hospedados; para Intern, conta as 1.010 passagens locais reportadas. Os arquivos e critérios de inclusão estão explícitos em `reanalyze.py`; os totais são reconciliados com os resultados publicados por `tests/bench/test_cost_per_correct.py`.

## O que isto não mostra

- Custo de GPU e energia local não está incluído; por isso, custo zero significa custo de API zero, não custo operacional total zero.
- Custos de API são os do dia da medição e podem mudar.
- A métrica não compara latência, qualidade além da contagem definida acima, nem custos humanos de hand-off.
