"""JSON logs with a request id, and optional OpenTelemetry tracing and metrics.

Logs carry ids, outcomes and durations. Message text and tool arguments are customer data and
are logged only at DEBUG. Every line passes through `redact`, so a secret that ends up in an
error message is not written out.

Tracing and metrics are off unless OTEL_EXPORTER_OTLP_ENDPOINT is set and the `otel` extra is
installed; without them `span` and `count` do nothing.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
import time
from typing import Any, Iterable, Iterator

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)

_SECRETS: tuple[str, ...] = ()
_tracer: Any = None
_meter_instruments: dict[str, Any] = {}


def redact(text: str) -> str:
    for secret in _SECRETS:
        if len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text


class _JsonFormatter(logging.Formatter):
    converter = time.gmtime  # log times in UTC

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        entry.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, default=str))


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever `sys.stdout` is at the time, so a replaced stdout is followed."""

    @property  # type: ignore[override]
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup(level: str, secrets: Iterable[str]) -> None:
    global _SECRETS
    _SECRETS = tuple(secrets)
    handler = _StdoutHandler()
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    _setup_otel()


def _setup_otel() -> None:
    global _tracer
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the otel extra is not installed"
        )
        return
    resource = Resource.create(
        {"service.name": os.environ.get("OTEL_SERVICE_NAME", "support-agent")}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("support-agent")
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )
    metrics.set_meter_provider(meter_provider)
    meter = metrics.get_meter("support-agent")
    _meter_instruments["requests"] = meter.create_counter("support_agent.requests")
    _meter_instruments["request_seconds"] = meter.create_histogram(
        "support_agent.request.duration", unit="s"
    )
    _meter_instruments["tool_errors"] = meter.create_counter("support_agent.tool_errors")
    _meter_instruments["handoffs"] = meter.create_counter("support_agent.handoffs")


@contextlib.contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name, attributes=attributes):
        yield


def count(name: str, value: float = 1, **attributes: Any) -> None:
    instrument = _meter_instruments.get(name)
    if instrument is None:
        return
    if hasattr(instrument, "add"):
        instrument.add(value, attributes)
    else:
        instrument.record(value, attributes)


def log(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    logger.log(level, event, extra={"fields": fields})


@contextlib.contextmanager
def timed() -> Iterator[dict[str, float]]:
    result = {"seconds": 0.0}
    start = time.monotonic()
    try:
        yield result
    finally:
        result["seconds"] = round(time.monotonic() - start, 3)
