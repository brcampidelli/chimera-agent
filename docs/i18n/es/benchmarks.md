---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmarks — demostrando la mejora del modelo débil

La tesis de Chimera es que la estructura hace que un modelo **débil/barato** rinda por encima de
su peso. La forma honesta de demostrarlo es un A/B controlado sobre un benchmark estándar: fijar
el subconjunto de tareas y el modelo, hacer que la **única** variable sea el andamiaje
(scaffolding), y reportar la delta con un intervalo de confianza — no un simple "mejoró". (La
investigación independiente encuentra que el mismo modelo oscila ~7 puntos solo por el
scaffolding, así que una puntuación sin cualificar no dice nada sobre *tu* contribución.)

## El experimento

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — tarea Docker + instrucción +
pruebas de verificación, calificadas pass/fail por esas pruebas, impulsadas por el harness
agnóstico de agente **Harbor**.

- **Brazo A (baseline):** un modelo gratuito en el scaffold neutral de Harbor — "modelo débil
  solo".
- **Brazo B (tratamiento):** el **mismo** modelo, los **mismos** IDs de tarea, impulsado por
  Chimera.
- **Métrica:** pass@1. **Titular:** Δ = rate(B) − rate(A), con un IC del 95%.
- **Guardas de honestidad:** fijar el subconjunto de IDs de tarea (publicarlo), correr ≥3
  semillas, publicar todas las transcripciones, y agregar una fila de modelo de frontera solo
  como *referencia de techo* — nunca como la comparación.

### El resultado — y fue en nuestra contra

Esta página cerraba la sección nombrando el número que *probaría* la tesis: "modelo gratuito solo =
X%, modelo gratuito + Chimera = Y%, Y ≫ X". El experimento ya se ejecutó, y Y salió **por debajo**
de X. En un subconjunto pre-registrado de N=40 con el mismo modelo en ambos brazos
(`deepseek-chat-v3.1`): **7,5% → 2,5%**, **Δ emparejada −5,0pp, IC 95% [−5,0%, +1,6%] — no
significativo**. El andamiaje no elevó a un modelo ya competente; ambos brazos quedan en un suelo
dominado por la varianza. Informe completo, incluido el pre-registro escrito antes de la ejecución:
[`bench/terminal_bench/RESULTS.md`](../../../bench/terminal_bench/RESULTS.md).

La frase que prometía `Y ≫ X` sobrevivió a la ejecución que la refutó, en esta página y en nueve
traducciones. Queda registrada aquí en lugar de borrarse en silencio, porque un proyecto cuyo único
activo real es la medición honesta no puede permitirse una página que predice lo contrario de su
propio resultado.

## Cómo ejecutarlo

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera se conecta como el agente de tratamiento vía `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` construye un `BaseAgent` de Harbor que ejecuta `chimera solve`
con las flags de scaffolding). Apunta Harbor a un subconjunto fijado y a un modelo gratuito para
cada brazo; consulta la [documentación de Harbor](https://www.tbench.ai/) para la invocación
exacta de `harbor run` y `--agent-import-path`.

## SWE-bench Verified (el segundo marcador) — **ejecutado cuatro veces**

Terminal-Bench prueba la tesis en tareas de CLI; SWE-bench la prueba en correcciones de bugs
reales de GitHub — dado un repo en un commit base y un issue, el agente debe producir un parche
que haga pasar las pruebas `FAIL_TO_PASS` de la instancia manteniendo en verde las
`PASS_TO_PASS`. "Verified" es el subconjunto validado por humanos.

### Resultados

Cuatro ejecuciones pre-registradas sobre slices de
`django/django` (el estrato de dificultad más fácil), `deepseek-chat-v3.1`, pass@1, calificadas
**únicamente** por el harness oficial `swebench` 4.1.0 en Docker. Informe completo:
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| ejecución | baseline | + Chimera | Δ pareada | IC 95% | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | no significativo |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | no significativo |
| **3 (replicación)** | 34.1% (14/41) | **43.9% (18/41)** | **+9.8%** | [−3.5%, +16.7%] | no significativo |
| **agrupado (secundario)** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **significativo** |
| 4 (atribución) | 34.1% | *solo el andamiaje* 39.0% | +4.9% | [−7.6%, +14.2%] | no significativo |

La ejecución 1 es un **cero exacto** y se publica sin cambios. La ejecución 2 corrigió dos fallos
que eran *nuestros* — el scaffold corría sin su mecanismo más fuerte, y 8 pasos de llamada a
herramientas no bastan para navegar un repositorio de 250 MB — y terminó con **3 instancias
ganadas, 0 perdidas**. El par es el hallazgo: el scaffold no vale *nada* cuando el agente está
privado de pasos, y vale *tres instancias* cuando no lo está, y gana editando **mejor** (69% vs.
57% de precisión cuando edita), no editando más.

> ⚠️ **Ninguno de estos es una puntuación de SWE-bench Verified.** El slice es deliberadamente fácil y de
> un solo repo, elegido para que un A/B pareado tenga margen de medición; una puntuación Verified
> real necesita el conjunto completo de 500. Y la delta **no es significativa** — con 8 pares
> ambos-fallan, n=19 deja solo tres pares informativos.

La ejecución 2 también trae una **retractación**: el mecanismo que habíamos rastreado para los
parches vacíos de la ejecución 1 estaba equivocado (la corrección fue el presupuesto de pasos, no
el diff-gate al que culpamos), corregido con el mismo protagonismo con que se afirmó.

Ese 3–0 sobre tres pares informativos es justo la forma que produce una muestra afortunada, y el
pre-registro le daba **una posibilidad entre tres de ser solo eso**. Así que la ejecución 3 lo
repitió sobre **41 instancias cuyos resultados nunca habíamos visto**, sin cambiar nada más. El
efecto **reapareció**: +9,8%, dentro de la banda registrada de +5 a +20, en un slice que resultó
*más difícil* que el de la ejecución 2 (baseline 34,1% vs 42,1%). La ejecución 4 separó luego el
andamiaje del diff-gate sobre las mismas 41: **+4,9% cada uno**, y el mecanismo es la precisión, que
sube 50% → 59% → 67% mientras la tasa de parches no se mueve. Ninguna ejecución individual es
significativa; el agrupado n=60 sí — y se pre-registró como **secundario** precisamente porque
mezcla datos vistos con no vistos.

### El adaptador

El adaptador (`chimera.eval.swe_bench`) es honesto sobre su frontera: las partes puras — la
invocación de `chimera solve` por instancia (brazo de tratamiento) y el parseo del reporte de
evaluación oficial — viven aquí y tienen pruebas unitarias; el dataset y el harness de evaluación
Docker son **opcionales y no se incluyen**, y el veredicto pass/fail proviene de las propias
pruebas de SWE-bench, nunca autorreportado.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Ambos reportes se proyectan sobre la lista de instancias compartida (un id faltante cuenta como
no resuelto), así que los dos brazos siempre se comparan sobre instancias idénticas — y luego se
aplica el mismo veredicto de Newcombe-CI.

## Puntuar el A/B (sin necesidad de benchmark)

Una vez que cada brazo produjo pass/fail por tarea, la estadística es un solo comando — esto no
necesita **nada extra**, así que el motor de reporte honesto siempre está disponible:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Cada archivo es una lista JSON de booleanos (o `{task_id: bool}`) sobre los **mismos** IDs de
tarea. Salida: la tasa de aprobación acotada por Wilson de cada brazo, la delta, su IC de
Newcombe al 95%, y si la diferencia es **significativa** (el IC excluye el cero). Si no lo es, se
reporta con claridad — un subconjunto más grande / más semillas, o la funcionalidad genuinamente
no mueve el número.

Este mismo `bench-compare` es la vara de medir para cada funcionalidad posterior: cada adición de
M14 debe demostrar que mueve la Δ sobre el subconjunto idéntico, o se elimina.

## La trampa honesta (qué evitar)

- **Contaminación** — el SWE-bench público tiene fugas de solución documentadas; preferir
  conjuntos resistentes a la contaminación y reportar la salvedad.
- **Confusión de scaffold** — nunca reportar un crudo "obtuvimos X%"; solo la delta del A/B
  aísla la contribución de Chimera.
- **Baseline equivocada / selección sesgada** — comparar débil+Chimera con el *mismo modelo
  débil solo*, sobre los IDs de tarea *idénticos*, con semillas y logs completos. Un modelo de
  frontera es un techo, no un rival.
