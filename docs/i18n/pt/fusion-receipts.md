---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Recibos de fusão — "fusão seletiva com recibos"

O núcleo de raciocínio do Chimera mistura um **painel** de modelos (painel → juiz → sintetizador).
A fusão compra qualidade mas custa mais tokens, então a pergunta honesta nunca é "a fusão é boa?",
mas "**valeu a pena, aqui?**". Os recibos respondem isso com números em vez de uma alegação.

Toda execução de fusão pode ser precificada em um **recibo**: quanto cada conselheiro (membro do
painel), o juiz, e o sintetizador custaram — cada um na tarifa *do seu próprio* modelo — mais se o
modo seletivo interrompeu o painel antes do tempo. Persista os recibos e você tem uma **curva de
custo × qualidade** publicável.

## Experimente

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` reporta a **taxa de fusão** (com que frequência o painel completo de fato rodou
vs. uma interrupção seletiva), o custo médio/total sobre as execuções que tinham um preço
conhecido, e — quando os recibos carregam um sinal de qualidade pass/fail — a taxa de aprovação e
os **dólares por resposta aprovada**.

## Regras de honestidade (por construção)

- **Tokens são medidos; dólares são estimados.** As contagens de tokens vêm do provedor; o valor em
  dólares é calculado a partir do **preço de tabela** público aproximado, então um recibo é um
  estimador, não uma fatura.
- **Modelo desconhecido → custo desconhecido, nunca zero.** Se qualquer etapa roda um modelo sem
  preço registrado, o total do recibo é `None` (`unknown`), então um preço ausente não pode se
  disfarçar de "grátis". Os preços podem ser sobrescritos no código (`chimera.fusion.set_price`).
- **Atribuição por conselheiro.** O custo do painel é discriminado *por modelo*
  (`receipt.advisor_costs`), então dá para ver qual conselheiro se pagou — a substância por trás da
  fusão seletiva, não um slogan.

## Por que isso existe

O campo se moveu na direção de roteamento/cascatas (gastar mais só quando o risco justifica), e se
afastou da fusão sempre-ligada. Os recibos são o que permite ao Chimera fundir **seletivamente e
provar que valeu a pena** — a curva de custo×qualidade é a evidência, publicada inclusive nas
execuções em que a fusão *não* ajudou.
