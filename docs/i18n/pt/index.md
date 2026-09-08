---
source_sha256: 16ac522d3f1fdc1a508d268b9f88b22f75c896bd1ae6dd87839afd256e4d745f
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

- **:material-account-switch: Conduza outro agente**
  Entregue um turno ao Claude Code ou ao Gemini CLI via ACP — e leia o que isso faz com as
  garantias.
  [Agentes externos →](external-agents.md)

- **:material-server: Opere-o**
  Rode 24/7 em um servidor pequeno; agende jobs; entregue em chat.
  [Deploy →](deploy.md)

- **:material-shield-lock: Segurança**
  Governança, sandbox, rastreamento de taint — e seus limites honestos.
  [Segurança →](security.md) · [Auditoria de canais dormentes →](audits/sleeper-channels.md)

- **:material-sitemap: Entenda-o**
  Como o núcleo de fusão, a evolução, e as camadas de segurança se encaixam.
  [Arquitetura →](architecture.md)

- **:material-console: Todo comando**
  Os 79, gerados a partir da própria CLI — incluindo os 33 que não apareciam em documentação
  nenhuma.
  [Referência de comandos →](commands.md)

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
