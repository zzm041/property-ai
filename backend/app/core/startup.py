"""Startup assembly self-checks.

PA-104-A-1: production must never start with the durable task path disabled.
Without the durable outbox an enqueue failure is either raised after the
business rows are written (PA-104-A) or, worse, silently lost. The check
belongs to assembly (``create_app``), not to individual call sites.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

DURABLE_TASKS_FIX_HINT = (
    "Set DURABLE_TASKS_ENABLED=true in production so task intents are persisted "
    "before the worker hand-off (see docs/09-DEPLOYMENT.md)."
)


def verify_task_delivery_configuration(settings: Settings) -> None:
    """Refuse to assemble a production app without durable task delivery."""

    if settings.app_env != "production":
        return
    if settings.durable_tasks_enabled:
        return

    message = (
        "DURABLE_TASKS_ENABLED must be true when APP_ENV=production: without the "
        "durable task path a failed enqueue leaves a committed business row with no "
        "queued task and no persisted intent. " + DURABLE_TASKS_FIX_HINT
    )
    logger.error(
        "durable_task_delivery_disabled",
        app_env=settings.app_env,
        durable_tasks_enabled=bool(settings.durable_tasks_enabled),
        fix=DURABLE_TASKS_FIX_HINT,
    )
    raise RuntimeError(message)


# t165: the RAG chain has four providers whose "development" variants answer
# from local arithmetic (lexical overlap, deterministic pseudo-vectors) instead
# of a model. That is what a test wants and what production must never ship:
# citations and scores look computed while no model ran, and every downstream
# check (exactness, permission filtering, evidence excerpts) passes anyway.
# Assembly therefore refuses to build the app when a production environment
# would serve a synthetic RAG answer.
RAG_PROVIDER_FIX_HINT = (
    "Set EMBEDDING_PROVIDER=production, RERANKER_PROVIDER=production and "
    "RAG_LLM_PROVIDER=production (each with its endpoint) when APP_ENV=production; "
    "the development providers answer from local arithmetic instead of a model, "
    "so citations and relevance scores would be fabricated without any call."
)


def rag_provider_problems(settings: Settings) -> list[str]:
    """Return every production RAG provider that would answer without a model."""

    if settings.app_env != "production":
        return []
    problems: list[str] = []
    for label, mode, endpoint in (
        ("embedding", settings.embedding_provider, settings.embedding_endpoint),
        ("reranker", settings.reranker_provider, settings.reranker_endpoint),
    ):
        if mode != "production":
            problems.append(f"{label}_provider={mode} (no model is called)")
        elif not endpoint.strip():
            problems.append(f"{label}_provider=production without an endpoint")
    rag_llm = settings.rag_llm_provider
    if rag_llm == "inherit":
        # Inheriting a development work-order model hands the answer to the
        # same synthetic provider, so the inheritance target decides.
        if settings.llm_provider != "production":
            problems.append(
                f"rag_llm_provider=inherit while llm_provider={settings.llm_provider}"
            )
    elif rag_llm != "production":
        problems.append(f"rag_llm_provider={rag_llm} (no model is called)")
    return problems


def _synthetic_provider_names() -> tuple[str, ...]:
    from app.provider.embedding import DevelopmentEmbeddingProvider
    from app.provider.llm import DevelopmentLLMProvider
    from app.provider.reranker import DevelopmentRerankerProvider

    return (
        DevelopmentEmbeddingProvider.__name__,
        DevelopmentRerankerProvider.__name__,
        DevelopmentLLMProvider.__name__,
    )


def _injected_client(provider: object) -> object | None:
    """The HTTP providers accept a pre-built client; tests inject a mock one.

    A production assembly must reach a real socket, so an injected transport is
    treated as a fake by the same rule that rejects the development providers.
    """

    return getattr(provider, "_client", None)


def verify_rag_providers_are_real(
    settings: Settings,
    *,
    providers: Sequence[tuple[str, object]] | None = None,
) -> None:
    """Refuse to assemble a production app on synthetic RAG providers.

    ``providers`` lets the caller pass the objects assembly actually built; when
    it is omitted the guard only inspects the settings, which is enough to catch
    the misconfiguration this check exists for.
    """

    problems = rag_provider_problems(settings)
    synthetic: list[str] = []
    for name, provider in providers or ():
        synthetic_names = _synthetic_provider_names()
        if type(provider).__name__ in synthetic_names:
            synthetic.append(f"{name}={type(provider).__name__}")
            continue
        client = _injected_client(provider)
        if client is not None:
            synthetic.append(f"{name}={type(provider).__name__}(injected client)")
    problems.extend(
        f"{item} is a synthetic provider" for item in synthetic
    )
    if not problems:
        return

    message = (
        "RAG providers must call real models when APP_ENV=production: "
        + "; ".join(problems)
        + ". "
        + RAG_PROVIDER_FIX_HINT
    )
    logger.error(
        "rag_provider_not_real",
        app_env=settings.app_env,
        problems=problems,
        fix=RAG_PROVIDER_FIX_HINT,
    )
    raise RuntimeError(message)
