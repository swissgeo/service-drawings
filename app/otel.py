"""OpenTelemetry instrumentation setup for logs, traces, and metrics.

Initializes OTEL providers and exporters at import time to support uvicorn's
logging.dictConfig handler resolution. Provides functions to instrument
third-party libraries (aioboto3, FastAPI) and to cleanly shut down all
providers on application shutdown.
"""

import logging

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import AiobotocoreInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from fastapi import FastAPI

from app.settings import get_settings

_resource = Resource.create({"service.name": "service-drawings"})


def _get_providers() -> tuple[LoggerProvider | None, TracerProvider | None]:
    settings = get_settings()

    if settings.otel_sdk_disabled:
        return None, None
    # Log provider can be used together with logging instrumentation to send logs to the OTEL
    # configured exporter in the correct OTEL format
    log_provider = LoggerProvider(resource=_resource)
    set_logger_provider(log_provider)

    # Trace provider
    trace_provider = TracerProvider(resource=_resource)
    trace.set_tracer_provider(trace_provider)

    return log_provider, trace_provider


def _get_exporters() -> tuple[
    OTLPLogExporter | None,
    OTLPSpanExporter | None,
    OTLPMetricExporter | None,
]:
    settings = get_settings()

    if settings.otel_sdk_disabled and not settings.otel_enable_otlp_exporter:
        return None, None, None

    logs_exporters = OTLPLogExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=settings.otel_exporter_otlp_headers,
        insecure=settings.otel_exporter_otlp_insecure,
    )
    span_exporters = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=settings.otel_exporter_otlp_headers,
        insecure=settings.otel_exporter_otlp_insecure,
    )
    metric_exporters = OTLPMetricExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=settings.otel_exporter_otlp_headers,
        insecure=settings.otel_exporter_otlp_insecure,
    )

    return logs_exporters, span_exporters, metric_exporters


def _setup_log_processors(
    provider: LoggerProvider | None,
    exporter: OTLPLogExporter | None,
) -> None:
    if provider is None:
        return

    if exporter:
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))


def _setup_span_processors(
    provider: TracerProvider | None,
    exporter: OTLPSpanExporter | None,
) -> None:
    if provider is None:
        return

    if exporter:
        provider.add_span_processor(BatchSpanProcessor(exporter))


def _setup_metrics(exporter: OTLPMetricExporter | None) -> MeterProvider | None:
    settings = get_settings()

    if settings.otel_sdk_disabled or not settings.otel_enable_metrics:
        return None

    # The periodic exporter can be configured via environment variable:
    # OTEL_METRIC_EXPORT_INTERVAL [ms] => default to 60'000
    # OTEL_METRIC_EXPORT_TIMEOUT [ms] => default to 30'000
    metric_readers = [PeriodicExportingMetricReader(exporter)] if exporter else []

    meter_provider = MeterProvider(
        metric_readers=metric_readers,
        resource=_resource,
    )
    metrics.set_meter_provider(meter_provider)

    return meter_provider


# ------------------------------------------------------------------------------
# NOTE: Import-time setup is intentional.
#
# This allows uvicorn's logging.dictConfig() to resolve:
#
#   handlers:
#     otel:
#       (): app.otel.get_otel_handler
#
# At that point, get_otel_handler() must be importable and must already have access
# to an initialized LoggerProvider.

log_provider, trace_provider = _get_providers()

log_exporter, span_exporter, metric_exporter = _get_exporters()

_setup_log_processors(log_provider, log_exporter)
_setup_span_processors(trace_provider, span_exporter)

meter_provider = _setup_metrics(metric_exporter)


def get_otel_handler() -> logging.Handler:
    """Get the OTEL logging Handler."""
    settings = get_settings()

    if settings.otel_sdk_disabled:
        raise ValueError(
            "Cannot use OTEL handler in logging configuration when OTEL_SDK_DISABLE is true"
        )
    if log_provider is None:
        raise ValueError("OTEL log provider is not available")

    return LoggingHandler(logger_provider=log_provider)


def initialize_instrumentation(app: FastAPI) -> None:
    """Initialize OTEL instrumentation.

    Setup OTEL tracing functionalities for third party libraries.
    """
    settings = get_settings()

    if settings.otel_sdk_disabled:
        return

    # Setup tracing instrumentation
    if settings.otel_enable_boto:
        AiobotocoreInstrumentor().instrument()
    if settings.otel_enable_fastapi:
        FastAPIInstrumentor.instrument_app(app)


def shutdown_otel() -> None:
    """Flush and shutdown OTEL providers/processors on application shutdown."""
    if trace_provider is not None:
        trace_provider.shutdown()

    if log_provider is not None:
        log_provider.shutdown()

    if meter_provider is not None:
        meter_provider.shutdown()
