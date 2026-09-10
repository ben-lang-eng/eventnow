"""LLM integration for EventNow's AI-assisted features.

Wraps a LangChain pipeline (prompt | ChatOpenAI) used by the
"Polish with AI" feature on the event and session forms.
"""
import os
import time

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from opentelemetry import metrics

# Instruments come from the global meter provider. Until telemetry.py has
# installed a real provider these are no-ops (the OTel API's proxy meter),
# so recording below is always safe — with or without a Grafana account.
#
# Names follow the OTel-to-Prometheus translation: a counter named
# "polish_requests" lands in Grafana as polish_requests_total, and a
# histogram named "polish_latency" with unit "s" as polish_latency_seconds.
_meter = metrics.get_meter("eventnow.ai")
_polish_requests = _meter.create_counter(
    "polish_requests",
    description='"Polish with AI" requests, by outcome and error type.',
)
# Default histogram buckets are tuned for milliseconds; LLM round trips are
# 0.5-10 s, so without these bounds every sample lands in one bucket and
# histogram_quantile() has nothing to interpolate with.
_polish_latency = _meter.create_histogram(
    "polish_latency",
    unit="s",
    description="Latency of successful LLM polish calls.",
    explicit_bucket_boundaries_advisory=[0.25, 0.5, 1, 2, 4, 8, 15, 30],
)


def polish_event_description(rough_text: str, event_title: str = "") -> str:
    """Rewrite a rough event/session description as polished marketing copy.

    Args:
        rough_text: The organiser's rough draft description.
        event_title: Optional title, passed to the model for context.

    Returns:
        The polished description as plain text (roughly 100-150 words).

    Raises:
        Exception: Any error from the OpenAI API (auth failure, rate
            limit, etc.) propagates to the caller, which surfaces it
            in the HTMX partial.
    """
    # API key is read from LLM_API_KEY. Works with any OpenAI-compatible
    # provider (Groq, OpenRouter, Together, OpenAI itself) — just change
    # base_url and model name.
    # The model name is an env var, not a literal, because of the 2026-08
    # incident: Groq moved llama-3.3-70b-versatile to their Enterprise tier
    # and every developer key started 404-ing. Fixing that meant a code
    # change and a redeploy; as an env var the next such move is a dashboard
    # edit and a restart. openai/gpt-oss-20b is the default — the fastest
    # free-tier model still available, and ample for paragraph rewriting.
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL", "openai/gpt-oss-20b"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
    )

    # System message sets the copywriter persona + constraints;
    # human message carries the organiser's title and rough draft.
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are a marketing copywriter for an event-management platform. "
            "Rewrite the organiser's rough event description as polished, "
            "engaging marketing copy of about 100-150 words. Keep every factual "
            "detail (dates, prices, locations, names). Use clear, energetic "
            "language with dot points if appropriate. "
            "Avoid clichés like 'unforgettable journey' or 'one-of-a-kind'. "
            "Return only the polished description; no preamble or sign-off.",
        ),
        (
            "human",
            "Event title: {title}\n\nRough description:\n{rough}\n\nPolished description:",
        ),
    ])

    # Compose prompt + model into a runnable chain, then invoke with the
    # template variables. response.content holds the model's text reply.
    #
    # The metrics encode the 2026-08 Groq lesson: outcome and exception
    # class as labels (a bounded set — never raw messages or user data),
    # so a retired model shows up as error_type="NotFoundError" within one
    # export interval. The exception itself still propagates; recording a
    # failure must never replace raising it.
    chain = prompt | llm
    start = time.perf_counter()
    try:
        response = chain.invoke(
            {"title": event_title or "(no title)", "rough": rough_text})
    except Exception as exc:
        _polish_requests.add(
            1, {"outcome": "error", "error_type": type(exc).__name__})
        raise
    # Latency is recorded for successes only: the histogram answers "how
    # slow is a working polish?", while failure modes (timeouts included)
    # are the counter's job.
    _polish_latency.record(time.perf_counter() - start)
    _polish_requests.add(1, {"outcome": "success"})
    return response.content.strip()
