"""
Open Brain — OpenTelemetry instrumentation layer.
Follows the Observability Archetype constitution (see the
archetype-orchestrator project).

Every MCP tool call, DB query, and dashboard refresh produces:
  - A correlated OTel span (exported to OTLP + otel-traces.jsonl)
  - A structured log entry with trace_id + span_id
  - Counter/histogram metric increments

Environment variables:
  OTEL_SERVICE_NAME              default: open-brain
  OTEL_SERVICE_VERSION           default: 0.4.1
  OTEL_EXPORTER_OTLP_ENDPOINT    default: http://localhost:4317
  OTEL_DEV                       set to 1 for ConsoleSpanExporter
"""
import os
import json
import time
import logging
import asyncio
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps
from typing import Callable, Any

# ── OTel imports ──────────────────────────────────────────────────────────────
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor, ConsoleSpanExporter, SpanExporter, SpanExportResult,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter, PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import StatusCode

# ── Config ────────────────────────────────────────────────────────────────────
SERVICE  = os.getenv("OTEL_SERVICE_NAME",     "open-brain")
VERSION  = os.getenv("OTEL_SERVICE_VERSION",  "0.4.1")
OTLP_EP  = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4317")
DEV      = os.getenv("OTEL_DEV", "0") == "1"
BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"

_resource = Resource.create({
    "service.name":    SERVICE,
    "service.version": VERSION,
    "deployment.environment": "dev" if DEV else "prod",
})


# ── JSONL span exporter (always-on fallback) ──────────────────────────────────
class _JSONLSpanExporter(SpanExporter):
    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans):
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                for span in spans:
                    ctx = span.get_span_context()
                    f.write(json.dumps({
                        "ts":          datetime.now(timezone.utc).isoformat(),
                        "service":     SERVICE,
                        "span":        span.name,
                        "trace_id":    format(ctx.trace_id, "032x"),
                        "span_id":     format(ctx.span_id,  "016x"),
                        "status":      span.status.status_code.name,
                        "duration_ms": round((span.end_time - span.start_time) / 1e6, 2),
                        "attrs":       dict(span.attributes or {}),
                        "events":      [
                            {"name": e.name, "attrs": dict(e.attributes or {})}
                            for e in (span.events or [])
                        ],
                    }) + "\n")
        except Exception:
            pass
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass


# ── Providers ─────────────────────────────────────────────────────────────────
_tracer_provider: TracerProvider | None = None
_meter_provider:  MeterProvider  | None = None
_initialized = False


def initialize():
    """Call once at process start (after any fork). Idempotent."""
    global _tracer_provider, _meter_provider, _initialized
    if _initialized:
        return
    _initialized = True
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # ── Trace provider ────────────────────────────────────────────────────────
    _tracer_provider = TracerProvider(resource=_resource)

    # Always export to JSONL
    _tracer_provider.add_span_processor(
        BatchSpanProcessor(_JSONLSpanExporter(LOG_DIR / "otel-traces.jsonl"))
    )

    # OTLP gRPC exporter — opt-in only (set OTEL_OTLP_ENABLED=1 when a collector is running).
    # Without a collector, BatchSpanProcessor retries indefinitely, flooding server-crash.log
    # with UNAVAILABLE/DEADLINE_EXCEEDED errors and wasting CPU.
    if os.getenv("OTEL_OTLP_ENABLED", "0") == "1" and not DEV:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            _tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=OTLP_EP, insecure=True, timeout=2),
                    export_timeout_millis=3000,
                    schedule_delay_millis=10000,
                )
            )
        except Exception:
            pass

    if DEV:
        _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(_tracer_provider)

    # Metrics temporarily disabled (crashing on export)
    # TODO: Fix metrics exporter closed file issue
    pass

    # ── Auto-instrument psycopg2 + requests ──────────────────────────────────
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        Psycopg2Instrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except Exception:
        pass
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=True)
    except Exception:
        pass


# ── Public tracer + meter ─────────────────────────────────────────────────────
def _tracer():
    return trace.get_tracer(SERVICE, VERSION)

def _meter():
    return metrics.get_meter(SERVICE, VERSION)


# Lazy metric handles (created after initialize())
_calls_counter  = None
_errors_counter = None
_duration_hist  = None


def _get_metrics():
    global _calls_counter, _errors_counter, _duration_hist
    if _calls_counter is None:
        m = _meter()
        _calls_counter  = m.create_counter(f"{SERVICE}.calls.total",  description="Total tool/function calls")
        _errors_counter = m.create_counter(f"{SERVICE}.errors.total", description="Total errors")
        _duration_hist  = m.create_histogram(f"{SERVICE}.duration_ms", unit="ms", description="Call duration")
    return _calls_counter, _errors_counter, _duration_hist


# ── @instrument decorator ─────────────────────────────────────────────────────
def instrument(span_name: str | None = None):
    """
    Decorator that wraps a function (sync or async) in an OTel span.
    Records exceptions, sets status, and increments metrics.

    Usage:
        @instrument("mcp.remember")
        async def remember(...): ...

        @instrument()
        def fetch_stats(): ...
    """
    def decorator(fn: Callable) -> Callable:
        name = span_name or fn.__name__

        def _set_caller_attrs(span, kwargs):
            """Extract source/project from kwargs and add as span attributes."""
            src = kwargs.get("source", "")
            proj = kwargs.get("project", "")
            if src:
                span.set_attribute("mcp.source", src)
            if proj:
                span.set_attribute("mcp.project", proj)

        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs) -> Any:
                calls, errors, durations = _get_metrics()
                calls.add(1, {"tool": name})
                t0 = time.perf_counter()
                with _tracer().start_as_current_span(name) as span:
                    span.set_attribute("mcp.tool", name)
                    span.set_attribute("service.name", SERVICE)
                    _set_caller_attrs(span, kwargs)
                    try:
                        result = await fn(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        errors.add(1, {"tool": name})
                        raise
                    finally:
                        durations.record(
                            round((time.perf_counter() - t0) * 1000, 2),
                            {"tool": name},
                        )
            return async_wrapper
        else:
            @wraps(fn)
            def sync_wrapper(*args, **kwargs) -> Any:
                calls, errors, durations = _get_metrics()
                calls.add(1, {"tool": name})
                t0 = time.perf_counter()
                with _tracer().start_as_current_span(name) as span:
                    span.set_attribute("mcp.tool", name)
                    span.set_attribute("service.name", SERVICE)
                    _set_caller_attrs(span, kwargs)
                    try:
                        result = fn(*args, **kwargs)
                        span.set_status(StatusCode.OK)
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        errors.add(1, {"tool": name})
                        raise
                    finally:
                        durations.record(
                            round((time.perf_counter() - t0) * 1000, 2),
                            {"tool": name},
                        )
            return sync_wrapper

    return decorator


# ── Convenience: get current trace context for log correlation ─────────────────
def current_trace_context() -> dict:
    span = trace.get_current_span()
    ctx  = span.get_span_context()
    if ctx.is_valid:
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id":  format(ctx.span_id,  "016x"),
        }
    return {"trace_id": "", "span_id": ""}


# ── Tail helper for dashboard ─────────────────────────────────────────────────
def tail_traces(n: int = 50) -> list[dict]:
    """Read last N span records from otel-traces.jsonl for dashboard display."""
    path = LOG_DIR / "otel-traces.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in reversed(lines[-200:]):
            try:
                results.append(json.loads(line))
            except Exception:
                pass
            if len(results) >= n:
                break
        return list(reversed(results))
    except Exception:
        return []
