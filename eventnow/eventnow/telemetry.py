"""OpenTelemetry setup — the app reporting its own metrics (monitoring V2).

Called once from wsgi.py, before the WSGI handler is built, because the
Django auto-instrumentation inserts its timing middleware into
settings.MIDDLEWARE and must therefore run before Django loads the
middleware chain. runserver imports the same wsgi application, so local
dev goes through this path too.

Everything is env-driven and the whole module is a no-op unless
OTEL_EXPORTER_OTLP_ENDPOINT is set: local dev needs no Grafana account,
and observability must never be the thing that takes the app down. When
enabled, metrics are aggregated in-process and pushed to Grafana Cloud's
OTLP gateway over HTTPS every 60 s (push, not pull — Render's free tier
offers nothing for a Prometheus server to scrape). Export failures are
logged by the SDK's background thread and cost data, not uptime.

See monitoring/README.md for the architecture and the env vars.
"""

import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_initialised = False


def setup_telemetry():
    """Initialise OTel metrics export and Django auto-instrumentation.

    Reads configuration from the standard OTEL_* env vars. Safe to call
    when telemetry is unconfigured (does nothing) and safe to call twice
    (initialises once).

    Returns:
        bool: True when telemetry was switched on, False when skipped.
    """
    global _initialised
    if _initialised:
        return True

    # wsgi.py runs before settings.py's load_dotenv(), so under gunicorn the
    # .env file has not been read yet at this point. Idempotent and cheap.
    load_dotenv()

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False

    # Defaults for the two env vars nobody should have to remember:
    # - the probes must not pollute the RED metrics (a 2-minute synthetic
    #   check would dominate real traffic),
    # - opt in to the stable HTTP semantic conventions so the duration
    #   metric is http.server.request.duration in seconds, not the legacy
    #   milliseconds one — the dashboard queries assume seconds.
    os.environ.setdefault("OTEL_PYTHON_DJANGO_EXCLUDED_URLS", "healthz,readyz")
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

    # Imported lazily so an unconfigured process never pays for them.
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    # Resource.create() picks up OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES
    # from the environment; service.version ties every series to the deployed
    # build, mirroring the "version" field in /healthz/.
    resource = Resource.create(
        {"service.version": os.environ.get("RENDER_GIT_COMMIT", "dev")[:7]}
    )
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[reader])
    )
    DjangoInstrumentor().instrument()

    _initialised = True
    logger.info("OpenTelemetry metrics export enabled")
    return True
