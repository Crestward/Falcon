"""OpenTelemetry + LangSmith bootstrap. Call `setup_telemetry()` once at process start."""
from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from core.settings import get_settings

_log = logging.getLogger(__name__)
_tracer_provider: TracerProvider | None = None


def setup_telemetry(service_name: str | None = None) -> trace.Tracer:
    """Idempotent OTel + LangSmith init. Returns a tracer for the calling service."""
    global _tracer_provider
    settings = get_settings()
    name = service_name or settings.otel_service_name

    if _tracer_provider is None:
        resource = Resource.create({SERVICE_NAME: name})
        _tracer_provider = TracerProvider(resource=resource)

        try:
            exporter = OTLPSpanExporter(
                endpoint=settings.otel_exporter_otlp_endpoint,
                insecure=settings.otel_exporter_otlp_insecure,
            )
            _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as exc:  # pragma: no cover — collector may not be up in tests
            _log.warning("OTLP exporter init failed (%s); spans will be in-memory only.", exc)

        trace.set_tracer_provider(_tracer_provider)

        # LangSmith reads from env vars on first LangChain call. Just make sure
        # they're set before any chain is constructed.
        if settings.langchain_tracing_v2:
            os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
            os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)

    return trace.get_tracer(name)
