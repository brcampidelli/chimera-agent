---
source_sha256: fe1348e242b1851c75bb1525ecf723afe068c499ed372335aca5e281cc72ba04
---

# Chimera

Um agente de IA open-source (Apache-2.0), auto-evolutivo, cujo núcleo de raciocínio **funde
vários modelos** (painel → juiz → sintetizador) atrás de um roteador consciente de custo — com um
kernel de governança, um sandbox, e uma memória que aprende.

Este site é orientado a tarefas: escolha o que você quer fazer.

<div class="grid cards" markdown>

- **:material-rocket-launch: Comece agora**
  Instale, adicione uma chave, rode sua primeira tarefa em cinco minutos.
  [Instalação & primeira execução →](usage.md)

- **:material-toolbox: Faça algo de verdade**
  Recipes executáveis: triagem de e-mail, um resumo diário de pesquisa, um watchdog de
  repositório.
  [Recipes →](recipes.md)

- **:material-power-plug: Conecte tools**
  Plugue qualquer servidor MCP (GitHub, filesystem, …).
  [Servidores MCP →](mcp.md)

- **:material-server: Opere-o**
  Rode 24/7 em um servidor pequeno; agende jobs; entregue em chat.
  [Deploy →](deploy.md)

- **:material-shield-lock: Segurança**
  Governança, sandbox, rastreamento de taint — e seus limites honestos.
  [Segurança →](security.md)

- **:material-sitemap: Entenda-o**
  Como o núcleo de fusão, a evolução, e as camadas de segurança se encaixam.
  [Arquitetura →](architecture.md)

</div>

## A linha única

```bash
uv sync --extra dev && uv run chimera init
```

Depois experimente `chimera run "..."`, ou uma recipe de verdade:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Honesto por padrão

O Chimera está em **alpha**. Ele vem com defesa em profundidade, mas a documentação diz claramente
onde cada salvaguarda para — as defesas contra injection até publicam um número medido
(`chimera redteam`). Veja [Segurança](security.md).
