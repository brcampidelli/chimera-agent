---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Recibos de fusión — "fusión selectiva con recibos"

El núcleo de razonamiento de Chimera mezcla un **panel** de modelos (panel → juez →
sintetizador). La fusión compra calidad pero cuesta más tokens, así que la pregunta honesta
nunca es "¿es buena la fusión?" sino "**¿valió la pena, aquí?**". Los recibos responden eso con
números en lugar de una afirmación.

Cada ejecución de fusión se puede tasar en un **recibo**: cuánto costó cada asesor (miembro del
panel), el juez, y el sintetizador — cada uno a la tarifa de *su propio* modelo — más si el modo
selectivo cortocircuitó el panel. Persiste los recibos y obtienes una **curva de costo × calidad**
publicable.

## Pruébalo

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` reporta la **tasa de fusión** (con qué frecuencia el panel completo realmente
corrió frente a un cortocircuito selectivo), el costo medio/total sobre las ejecuciones que
tuvieron un precio conocido, y — cuando los recibos llevan una señal de calidad pass/fail — la
tasa de aprobación y los **dólares por respuesta aprobada**.

## Reglas de honestidad (por construcción)

- **Los tokens se miden; los dólares se estiman.** Los conteos de tokens vienen del proveedor;
  la cifra en dólares se calcula al **precio de lista** público aproximado, así que un recibo es
  un estimador, no una factura.
- **Modelo desconocido → costo desconocido, nunca cero.** Si alguna etapa ejecuta un modelo sin
  precio registrado, el total del recibo es `None` (`unknown`), así que un precio faltante no
  puede hacerse pasar por "gratis". Los precios se pueden sobreescribir en código
  (`chimera.fusion.set_price`).
- **Atribución por asesor.** El costo del panel se desglosa *por modelo*
  (`receipt.advisor_costs`), así puedes ver qué asesor se ganó su lugar — la sustancia detrás de
  la fusión selectiva, no un eslogan.

## Por qué existe esto

El campo se movió hacia el enrutamiento/cascadas (gastar más solo cuando el riesgo lo justifica),
y se alejó de la fusión siempre activa. Los recibos son lo que le permite a Chimera fusionar
**selectivamente y demostrar que valió la pena** — la curva costo×calidad es la evidencia,
publicada incluyendo las ejecuciones donde la fusión *no* ayudó.
