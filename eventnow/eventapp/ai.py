"""LLM integration for EventNow's AI-assisted features.

Wraps a LangChain pipeline (prompt | ChatOpenAI) used by the
"Polish with AI" feature on the event and session forms.
"""
import os

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate


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
    llm = ChatOpenAI(
        # Groq moved llama-3.3-70b-versatile to their Enterprise tier
        # (2026-08), so developer keys 404 on it. openai/gpt-oss-20b is
        # the fastest free-tier model still available and is more than
        # capable for paragraph rewriting.
        model="openai/gpt-oss-20b",
        api_key=os.getenv("LLM_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
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
    chain = prompt | llm
    response = chain.invoke(
        {"title": event_title or "(no title)", "rough": rough_text})
    return response.content.strip()
