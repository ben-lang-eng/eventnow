"""Operational health endpoints, polled by external monitoring.

Two endpoints, split along the Kubernetes liveness/readiness line so the
monitor can poll the cheap one often and the expensive one rarely:

    /healthz/   liveness  — is this process up and configured? No I/O at all.
    /readyz/    readiness — can it actually serve traffic? Runs SELECT 1.

The split is a cost decision, not a purity one. Neon's free tier bills
compute hours and suspends the database after ~5 minutes idle, so a probe
that ran SELECT 1 every minute would hold Postgres awake around the clock
and burn the monthly allowance on health checks alone. Liveness therefore
touches nothing; readiness touches the database and is polled every 15
minutes. See monitoring/README.md for the arithmetic.

Neither endpoint ever calls the LLM. A real completion per probe would cost
money and eat the provider's rate limit; the config check below only asserts
that the credentials are present, and reports which model the app would ask
for — the two facts that mattered during the 2026-08 Groq incident.
"""

import os

from django.db import connection
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


def _version():
    """Return the running build's short commit SHA.

    Render injects RENDER_GIT_COMMIT into the service environment on every
    deploy. Locally it is absent, so fall back to "dev".

    Returns:
        str: Seven-character commit SHA, or "dev" outside Render.
    """
    return os.environ.get("RENDER_GIT_COMMIT", "dev")[:7]


def _llm_config_check():
    """Check that the LLM feature is configured, without calling the provider.

    Only LLM_API_KEY is required; the model name and base URL have working
    defaults in ai.py.

    Returns:
        str: "ok" when the API key is set, otherwise "missing: LLM_API_KEY".
    """
    if not os.environ.get("LLM_API_KEY"):
        return "missing: LLM_API_KEY"
    return "ok"


def _llm_model():
    """Return the model name the app would use for the next polish request.

    Reported for diagnosis rather than checked: during the 2026-08 Groq
    incident the interesting question was "which model is production actually
    asking for?", and the only way to answer it was to read the deployed
    source. Now it is a curl away.

    Returns:
        str: The configured model name, mirroring ai.py's default.
    """
    return os.environ.get("LLM_MODEL", "openai/gpt-oss-20b")


def _database_check():
    """Run the cheapest possible round-trip against the default database.

    Returns:
        str: "ok", or "error: <exception class>". The exception message is
        deliberately dropped — connection errors embed the host, database
        name and sometimes credentials, and this endpoint is public.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 — any driver error means not ready
        return f"error: {type(exc).__name__}"
    return "ok"


def _envelope(status, checks):
    """Build the JSON body both endpoints return.

    Args:
        status: Overall verdict — "ok", "degraded" or "error".
        checks: Mapping of check name to result string.

    Returns:
        dict: The response body.
    """
    return {
        "status": status,
        "checks": checks,
        "version": _version(),
        "llm_model": _llm_model(),
    }


def _payload(checks):
    """Wrap check results in the response envelope both endpoints share.

    A check that failed hard ("error: ...") makes the service unhealthy and
    is worth a 503; a check that is merely misconfigured ("missing: ...")
    marks it degraded but still returns 200, because the site serves fine
    with the AI button broken and paging on that would be noise. The monitor
    catches degraded states with a body assertion on "status": "ok" instead.

    Args:
        checks: Mapping of check name to result string.

    Returns:
        tuple[dict, int]: The JSON payload and the HTTP status code to use.
    """
    if any(result.startswith("error") for result in checks.values()):
        return _envelope("error", checks), 503
    if any(result != "ok" for result in checks.values()):
        return _envelope("degraded", checks), 200
    return _envelope("ok", checks), 200


@require_GET
@never_cache
def healthz(request):
    """Liveness probe: the process is up and its configuration is complete.

    Performs no database query and no network call, so it is safe to poll
    once a minute forever.

    Args:
        request: The HttpRequest.

    Returns:
        JsonResponse: {"status", "checks", "version"}, always HTTP 200 unless
        a configured check hard-fails.
    """
    payload, status = _payload({"llm_config": _llm_config_check()})
    return JsonResponse(payload, status=status)


@require_GET
@never_cache
def readyz(request):
    """Readiness probe: the process can reach its database.

    Wakes a suspended Neon compute, so poll this sparingly — every 15
    minutes is plenty for a portfolio app.

    Args:
        request: The HttpRequest.

    Returns:
        JsonResponse: {"status", "checks", "version"}, HTTP 503 when the
        database is unreachable.
    """
    payload, status = _payload({
        "db": _database_check(),
        "llm_config": _llm_config_check(),
    })
    return JsonResponse(payload, status=status)


@require_GET
def status_dashboard(request):
    """Redirect to the public Grafana dashboard for this deployment.

    Grafana Cloud cannot be iframed (embedding is Enterprise-only), so the
    closest thing to "the dashboard on the site" is a stable local URL that
    302s to the externally shared dashboard. The target lives in the
    STATUS_DASHBOARD_URL env var: the share link never enters the repo, and
    rotating or revoking it is a dashboard edit, not a redeploy — the same
    pattern as LLM_MODEL.

    Args:
        request: The HttpRequest.

    Returns:
        HttpResponseRedirect: 302 to the shared dashboard.

    Raises:
        Http404: When STATUS_DASHBOARD_URL is not configured.
    """
    url = os.environ.get("STATUS_DASHBOARD_URL")
    if not url:
        raise Http404
    return HttpResponseRedirect(url)
