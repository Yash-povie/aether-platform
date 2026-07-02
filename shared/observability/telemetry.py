import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")


def setup_telemetry(app, service_name: str, engine=None) -> None:
    """
    Initialise OpenTelemetry tracing for a FastAPI application.

    Parameters
    ----------
    app:
        The FastAPI application instance to instrument.
    service_name:
        Logical service name that will appear in Jaeger / Grafana Tempo.
    engine:
        Optional SQLAlchemy async engine.  When provided, SQL queries are
        traced via ``SQLAlchemyInstrumentor`` using the underlying sync
        engine.

    The tracer provider is exported to *OTEL_EXPORTER_OTLP_ENDPOINT*
    (default ``http://jaeger:4317``) via OTLP/gRPC.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument FastAPI — adds spans for every HTTP request automatically
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    # Optionally instrument SQLAlchemy (requires opentelemetry-instrumentation-sqlalchemy)
    if engine is not None:
        try:
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

            # AsyncEngine wraps a sync engine; OTel needs the sync one
            sync_engine = getattr(engine, "sync_engine", engine)
            SQLAlchemyInstrumentor().instrument(engine=sync_engine, tracer_provider=provider)
            logger.info(
                "SQLAlchemy OTel instrumentation active",
                extra={"service": service_name},
            )
        except ImportError:
            logger.warning(
                "opentelemetry-instrumentation-sqlalchemy not installed; "
                "SQL tracing disabled"
            )

    logger.info(
        "OTel tracing initialised",
        extra={"service": service_name, "endpoint": OTEL_ENDPOINT},
    )