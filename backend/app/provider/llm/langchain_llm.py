"""PA-165: optional LangChain layer for the supervisor's tool-calling loop.

The supervisor agent keeps its own bounded loop — the server still decides which actions are
legal on a given turn, still executes every read tool itself, and still validates the returned
command. What this module adds is a *real* LangChain hop for the model call: the prompt and the
permitted actions become an LCEL chain (`prompt | model.bind_tools(tools)`), and the model's
answer comes back as a LangChain tool call that the planner maps onto the existing command
contract.

Nothing here is imported at application startup, so a plain install (no `langchain` extra)
behaves exactly as before. When the extra is missing every builder raises
`LangChainUnavailable`; callers must decide to fall back and must log why — this module never
swallows that state.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from pydantic import SecretStr

from app.core.config import Settings


class LangChainUnavailable(RuntimeError):
    """The optional `langchain` extra is not installed (or cannot be imported)."""


_INSTALL_HINT = 'pip install -e "backend[langchain]"'

_loaded: tuple[Any, Any] | None = None


def _load() -> tuple[Any, Any]:
    """Import the optional packages once, translating ImportError into a clear error."""

    global _loaded
    if _loaded is not None:
        return _loaded
    try:  # pragma: no cover - exercised through `optional_dependencies_available`
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise LangChainUnavailable(
            f"LangChain is not installed: {exc}. Install the optional extra with {_INSTALL_HINT}."
        ) from exc
    _loaded = (ChatPromptTemplate, ChatOpenAI)
    return _loaded


def optional_dependencies_available() -> bool:
    """True when the `langchain` extra can be imported. Never raises."""

    try:
        _load()
    except LangChainUnavailable:
        return False
    return True


def resolve_chat_endpoint(
    settings: Settings, *, base_url: str | None = None, api_key: str | None = None
) -> tuple[str, str]:
    """Resolve the OpenAI-compatible endpoint the same way every other provider does.

    `Settings.resolve_local_ai_key` is the reviewed rule for "explicit key first, otherwise the
    local adapter token — but only towards a loopback endpoint"; reusing it keeps LangChain from
    inventing a second credential path.
    """

    endpoint = base_url if base_url is not None else settings.llm_base_url
    explicit = api_key if api_key is not None else settings.llm_api_key
    if isinstance(explicit, str):
        explicit = SecretStr(explicit)
    resolved = settings.resolve_local_ai_key(explicit, endpoint)
    if not endpoint:
        raise LangChainUnavailable("LangChain chat model needs an endpoint (LLM_BASE_URL is empty)")
    if not resolved:
        raise LangChainUnavailable(
            "LangChain chat model needs a credential (LLM_API_KEY or LOCAL_AI_INTERNAL_TOKEN)"
        )
    return endpoint, resolved


def build_langchain_chat_model(
    settings: Settings,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int = 0,
    temperature: float = 0.0,
) -> Any:
    """Build a `ChatOpenAI` pointed at the reviewed OpenAI-compatible endpoint."""

    _, chat_openai = _load()
    endpoint, resolved_key = resolve_chat_endpoint(settings, base_url=base_url, api_key=api_key)
    return chat_openai(
        model=model or settings.supervisor_agent_model or settings.llm_model,
        base_url=endpoint,
        api_key=resolved_key,
        temperature=temperature,
        timeout=float(timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds),
        max_retries=max_retries,
        streaming=False,
    )


def build_tool_chain(
    tools: Sequence[Any],
    *,
    model: Any | None = None,
    prompt: Any | None = None,
    settings: Settings | None = None,
    system_prompt: str | None = None,
    timeout_seconds: float | None = None,
) -> Any:
    """Compose `prompt | model.bind_tools(tools)` and return the invocable Runnable.

    `tools` are LangChain tool objects (or anything `bind_tools` accepts); `prompt` may be a
    `ChatPromptTemplate` so callers can shape the turn themselves, otherwise a minimal
    system+human prompt derived from `system_prompt` is used.
    """

    chat_prompt, _ = _load()
    if model is None:
        if settings is None:
            raise LangChainUnavailable("build_tool_chain needs a model or settings to build one")
        model = build_langchain_chat_model(settings, timeout_seconds=timeout_seconds)
    if prompt is None:
        prompt = chat_prompt.from_messages(
            [
                (
                    "system",
                    system_prompt
                    or "You plan one bounded read-only step and answer with exactly one tool call.",
                ),
                ("human", "{input}"),
            ]
        )
    return prompt | model.bind_tools(list(tools))


def tool_call_to_command(message: Any) -> dict[str, Any] | None:
    """Map the first LangChain tool call on `message` onto the planner's command dict.

    Returns None when the model answered without a tool call, so the caller can surface an
    invalid-output error instead of guessing an action.
    """

    calls: Iterable[Any] = getattr(message, "tool_calls", None) or []
    for call in calls:
        if isinstance(call, dict):
            name = call.get("name")
            args = call.get("args") or {}
        else:  # pragma: no cover - other LangChain message shapes
            name, args = getattr(call, "name", None), getattr(call, "args", {}) or {}
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(args, dict):
            continue
        command: dict[str, Any] = {"action": name}
        command.update(args)
        return command
    return None
