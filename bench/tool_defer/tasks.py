"""Ten tasks, each needing a tool that deferral puts behind the proxy.

Authored against `PREREGISTRATION.md`, before either arm ran, and none authored by watching an arm
fail. Each carries a shell verifier that exits 0 or 1 — no model judges anything here.

Half are offline. The other half touch the network, deliberately: a bench made only of offline tasks
would never exercise `scrape` / `http_get`, which are the deferred tools an ordinary session is most
likely to reach for. A network task that fails for a network reason is recorded as such rather than
counted against the arm.

`run_shell` stays in the core in both arms, so several of these are solvable by shelling out to
Python. That is deliberate and it is why `PREREGISTRATION.md` makes "reached through the proxy" a
reported field: a B-arm pass that never touched `tool_list` routed around the thing under test.
"""

from __future__ import annotations

from typing import Any

#: Each task: a prompt, the files its workspace starts with, and a shell command that judges it.
TASKS: list[dict[str, Any]] = [
    {
        "name": "csv_total",
        "needs": "read_document",
        "network": False,
        "files": {
            "vendas.csv": "produto,valor\ncafe,12.50\ncha,8.25\nbolo,15.00\nsuco,9.75\n",
        },
        "prompt": (
            "Leia vendas.csv e escreva em total.txt apenas a soma da coluna valor, "
            "com duas casas decimais e nada mais."
        ),
        "verify": "python -c \"assert open('total.txt').read().strip()=='45.50'\"",
    },
    {
        "name": "json_chart",
        "needs": "render_chart",
        "network": False,
        "files": {"dados.json": '[{"mes":"jan","vendas":10},{"mes":"fev","vendas":25}]\n'},
        "prompt": (
            "Com os dados de dados.json, gere um grafico de barras e salve a imagem "
            "como grafico.png no diretorio de trabalho."
        ),
        "verify": "python -c \"import os;assert os.path.getsize('grafico.png')>1000\"",
    },
    {
        "name": "primes_sum",
        "needs": "execute_code",
        "network": False,
        "files": {},
        "prompt": (
            "Calcule a soma dos primeiros 100 numeros primos e escreva o resultado, "
            "apenas o numero, em soma.txt."
        ),
        "verify": "python -c \"assert open('soma.txt').read().strip()=='24133'\"",
    },
    {
        "name": "doc_field",
        "needs": "read_document",
        "network": False,
        "files": {
            "contrato.html": (
                "<html><body><h1>Contrato</h1><p>Prazo de entrega: 14 dias uteis.</p>"
                "<p>Multa: 2%.</p></body></html>\n"
            )
        },
        "prompt": "Segundo contrato.html, qual o prazo de entrega? Escreva so o numero em prazo.txt.",
        "verify": "python -c \"assert open('prazo.txt').read().strip()=='14'\"",
    },
    {
        "name": "stats_median",
        "needs": "code_interpreter",
        "network": False,
        "files": {"numeros.txt": "\n".join(str(n) for n in [7, 3, 9, 1, 5, 8, 2]) + "\n"},
        "prompt": (
            "Calcule a mediana dos numeros em numeros.txt e escreva apenas o valor em mediana.txt."
        ),
        "verify": "python -c \"assert open('mediana.txt').read().strip() in ('5','5.0')\"",
    },
    {
        "name": "fetch_title",
        "needs": "scrape",
        "network": True,
        "files": {},
        "prompt": (
            "Acesse https://example.com e escreva o titulo da pagina (o texto do h1) "
            "em titulo.txt, sem mais nada."
        ),
        "verify": "python -c \"assert 'Example Domain' in open('titulo.txt').read()\"",
    },
    {
        "name": "http_status",
        "needs": "http_get",
        "network": True,
        "files": {},
        "prompt": (
            "Faca uma requisicao HTTP GET para https://example.com e escreva o codigo de "
            "status HTTP, apenas o numero, em status.txt."
        ),
        "verify": "python -c \"assert open('status.txt').read().strip()=='200'\"",
    },
    {
        "name": "page_words",
        "needs": "scrape",
        "network": True,
        "files": {},
        "prompt": (
            "Leia o conteudo de https://example.com e escreva em contem.txt a palavra SIM se "
            "a pagina mencionar 'illustrative examples', ou NAO se nao mencionar."
        ),
        "verify": "python -c \"assert open('contem.txt').read().strip().upper()=='SIM'\"",
    },
    {
        "name": "arxiv_lookup",
        "needs": "arxiv_search",
        "network": True,
        "files": {},
        "prompt": (
            "Busque no arXiv por 'attention is all you need' e escreva em autor.txt o sobrenome "
            "do primeiro autor do artigo mais relevante."
        ),
        "verify": "python -c \"assert 'aswani' in open('autor.txt').read().strip().lower()\"",
    },
    {
        "name": "site_map",
        "needs": "map",
        "network": True,
        "files": {},
        "prompt": (
            "Liste as URLs disponiveis em https://example.com e escreva quantas encontrou, "
            "apenas o numero, em quantas.txt."
        ),
        "verify": "python -c \"assert open('quantas.txt').read().strip().isdigit()\"",
    },
]
