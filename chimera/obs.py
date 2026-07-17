"""Optional OpenTelemetry tracing + metrics — a no-op unless you turn it on.

An autonomous agent is a black box without traces. This is the seam :mod:`chimera.telemetry`
promised: opt-in OTLP spans and metrics, so you can see *what the agent did* (which tools, which
model, how many tokens, what it cost) in Jaeger / Tempo / Grafana / any OTLP backend.

**Off by default and zero-overhead.** Nothing here imports ``opentelemetry`` until
:func:`configure_otel` runs, and it only runs when you ask for it — set ``CHIMERA_OTEL=1`` (or the
standard ``OTEL_EXPORTER_OTLP_ENDPOINT``) *and* install the extra: ``pip install
'chimera-agent[otel]'``. Without both, :func:`span` and :func:`record_llm_metrics` are inert: they
take the same arguments and do nothing, so call sites never branch on whether tracing is on.

Instrumented seams today: every tool call (``tool.run`` span) and every agent run's token/cost
(``chimera.llm.*`` counters). New spans are one line — ``with span("name", **attrs): ...`` — so more
coverage is additive.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("obs")

_tracer: Any = None  # set by configure_otel(); None = tracing off (no-op)
_prompt_ctr: Any = None
_completion_ctr: Any = None
_cost_ctr: Any = None
_configured = False


class _Span:
    """Uniform handle yielded by :func:`span`. ``set`` is a no-op when tracing is off, so call sites
    can attach attributes computed after the work without checking whether OTel is active."""

    __slots__ = ("_s",)

    def __init__(self, s: Any = None) -> None:
        self._s = s

    def set(self, **attrs: Any) -> None:
        if self._s is None:
            return
        for key, value in attrs.items():
            if value is not None:
                self._s.set_attribute(key, value)


def configure_otel(settings: Settings | None = None) -> bool:
    """Initialise the OTLP tracer + meters if enabled and the extra is installed. Idempotent.

    Returns True when tracing is active. Safe to call unconditionally at startup: it does nothing
    (and imports nothing heavy) unless observability is turned on.
    """
    global _tracer, _prompt_ctr, _completion_ctr, _cost_ctr, _configured
    if _configured:
        return _tracer is not None
    _configured = True

    from chimera.config import get_settings

    settings = settings or get_settings()
    enabled = bool(getattr(settings, "otel", False)) or bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if not enabled:
        return False

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning(
            "observability is enabled (CHIMERA_OTEL / OTEL_EXPORTER_OTLP_ENDPOINT) but the [otel] "
            "extra is not installed; tracing stays off. Install: pip install 'chimera-agent[otel]'"
        )
        return False

    resource = Resource.create({"service.name": "chimera-agent"})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    _tracer = trace.get_tracer("chimera")

    meter_provider = MeterProvider(
        resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
    )
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter("chimera")
    _prompt_ctr = meter.create_counter("chimera.llm.prompt_tokens", unit="token")
    _completion_ctr = meter.create_counter("chimera.llm.completion_tokens", unit="token")
    _cost_ctr = meter.create_counter("chimera.llm.cost_usd", unit="usd")
    _log.info("OpenTelemetry enabled (OTLP): service.name=chimera-agent")
    return True


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[_Span]:
    """Open a span if tracing is active; a zero-overhead no-op otherwise.

    Yields a :class:`_Span` whose ``.set(**attrs)`` attaches attributes known only after the work.
    """
    if _tracer is None:
        yield _Span(None)
        return
    with _tracer.start_as_current_span(name) as raw:
        handle = _Span(raw)
        handle.set(**attrs)
        yield handle


def record_llm_metrics(
    *, model: str | None, prompt_tokens: int, completion_tokens: int, usd: float | None
) -> None:
    """Emit token + cost counters for one agent run. No-op when tracing is off."""
    if _prompt_ctr is None:
        return
    tags = {"llm.model": model or "unknown"}
    _prompt_ctr.add(max(0, prompt_tokens), tags)
    _completion_ctr.add(max(0, completion_tokens), tags)
    if usd is not None:
        _cost_ctr.add(usd, tags)
