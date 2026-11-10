"""PA-165: LangChain carries the supervisor's tool-orchestration turn, with a direct fallback.

No test here touches the network or a real model: the LangChain hop is exercised with an
in-memory chat model that records the tools it was asked to bind and answers with a canned tool
call, which is exactly the shape the bounded loop consumes.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.provider import langchain_llm
from app.provider.errors import ProviderInvalidOutputError, ProviderUnavailableError
from app.provider.supervisor_agent import LangChainSupervisorPlanner, SupervisorPlanner
from app.service.supervisor_agent_tools import build_supervisor_planner

LOCAL = "http://127.0.0.1:11437/v1"
REMOTE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _settings(**overrides) -> Settings:
    payload = {
        "app_env": "test",
        "llm_base_url": LOCAL,
        "llm_api_key": "llm-key",
        "supervisor_agent_base_url": LOCAL,
        "supervisor_agent_model": "qwen-test",
        "supervisor_agent_api_key": "supervisor-key",
    }
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


class _RecordingLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []
        self.infos: list[tuple[str, dict]] = []

    def error(self, event: str, **kwargs) -> None:
        self.errors.append((event, kwargs))

    def info(self, event: str, **kwargs) -> None:
        self.infos.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:  # pragma: no cover - parity only
        pass


class _FakeChain:
    """Stands in for the LCEL chain: records the payload, returns a canned message."""

    def __init__(self, message) -> None:
        self.message = message
        self.payloads: list[dict] = []

    def invoke(self, payload):
        self.payloads.append(payload)
        return self.message


# --------------------------------------------------------------------------- engine switch


def test_engine_defaults_to_direct_and_keeps_the_reviewed_planner():
    field_default = Settings.model_fields["supervisor_agent_engine"].default
    assert field_default == "direct"

    planner = build_supervisor_planner(_settings())

    assert type(planner) is SupervisorPlanner
    assert planner.mode in {"deterministic_stub", "real_model"}


def test_langchain_engine_without_the_extra_falls_back_and_logs_the_reason(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr("app.service.supervisor_agent_tools.logger", recorder)
    monkeypatch.setattr(langchain_llm, "optional_dependencies_available", lambda: False)

    planner = build_supervisor_planner(_settings(supervisor_agent_engine="langchain"))

    assert type(planner) is SupervisorPlanner
    assert recorder.errors, "a silent fallback is exactly what PA-165 must not do"
    event, fields = recorder.errors[0]
    assert event == "supervisor_agent_engine_unavailable"
    assert fields["engine"] == "langchain"
    assert fields["fallback_engine"] == "direct"
    assert "backend[langchain]" in fields["reason"]


def test_engine_switch_is_a_validated_choice():
    with pytest.raises(Exception):
        _settings(supervisor_agent_engine="langgraph")


# ------------------------------------------------------------------ endpoint/key resolution


def test_endpoint_resolution_rejects_a_missing_endpoint():
    with pytest.raises(langchain_llm.LangChainUnavailable) as caught:
        langchain_llm.resolve_chat_endpoint(_settings(llm_base_url=""))

    assert "LLM_BASE_URL" in str(caught.value)


def test_endpoint_resolution_rejects_a_missing_credential():
    with pytest.raises(langchain_llm.LangChainUnavailable) as caught:
        langchain_llm.resolve_chat_endpoint(_settings(llm_api_key="", local_ai_internal_token=""))

    assert "LLM_API_KEY" in str(caught.value)


def test_local_token_is_not_sent_to_a_remote_endpoint():
    settings = _settings(llm_api_key="", local_ai_internal_token="local-token", llm_base_url=REMOTE)

    with pytest.raises(langchain_llm.LangChainUnavailable):
        langchain_llm.resolve_chat_endpoint(settings)


def test_resolved_endpoint_passes_through_the_reviewed_key():
    endpoint, key = langchain_llm.resolve_chat_endpoint(_settings())

    assert endpoint == LOCAL
    assert key == "llm-key"


def test_absence_of_the_extra_is_proved_in_a_subprocess_with_the_import_blocked():
    """The important claim is "no extra installed still works", so simulate a real absence.

    A fresh interpreter with a meta-path blocker makes `import langchain_core` fail exactly as it
    would on a base install; the factory must then hand back the direct planner and say why.
    """

    import subprocess
    import sys
    from pathlib import Path

    script = """
import sys
class Blocker:
    def find_module(self, name, path=None):
        if name.split('.')[0] in {'langchain_core', 'langchain_openai', 'langchain'}:
            return self
        return None
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'langchain_core', 'langchain_openai', 'langchain'}:
            raise ImportError(f'blocked: {name}')
        return None
sys.meta_path.insert(0, Blocker())
from app.core.config import Settings
from app.provider import langchain_llm
from app.provider.supervisor_agent import LangChainSupervisorPlanner, SupervisorPlanner
from app.service.supervisor_agent_tools import build_supervisor_planner
settings = Settings(_env_file=None, app_env='test', supervisor_agent_engine='langchain',
                    supervisor_agent_base_url='http://127.0.0.1:11437/v1',
                    supervisor_agent_model='qwen-test', supervisor_agent_api_key='k')
print('available', langchain_llm.optional_dependencies_available())
try:
    LangChainSupervisorPlanner(settings)
except langchain_llm.LangChainUnavailable as exc:
    print('unavailable', 'backend[langchain]' in str(exc))
else:
    print('unavailable False')
planner = build_supervisor_planner(settings)
print('fallback', type(planner) is SupervisorPlanner)
"""

    backend_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=backend_root,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": str(backend_root), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )

    assert completed.returncode == 0, completed.stderr
    out = completed.stdout
    # structlog also writes to stdout, so match by prefix instead of fixed line numbers.
    assert "available False" in out
    assert "unavailable True" in out
    assert "fallback True" in out
    assert "supervisor_agent_engine_unavailable" in out
    assert "fallback_engine=direct" in out


# --------------------------------------------------------------------- command mapping


def test_tool_call_to_command_maps_the_first_tool_call():
    message = type(
        "Message",
        (),
        {"tool_calls": [{"name": "diagnose", "args": {"work_order_id": 7, "version": 2}, "id": "c1"}]},
    )()

    assert langchain_llm.tool_call_to_command(message) == {
        "action": "diagnose",
        "work_order_id": 7,
        "version": 2,
    }


def test_tool_call_to_command_returns_none_without_a_tool_call():
    assert langchain_llm.tool_call_to_command(type("Message", (), {"tool_calls": []})()) is None
    assert langchain_llm.tool_call_to_command(type("Message", (), {})()) is None


# ------------------------------------------------------- planner contract (no model needed)


def test_planner_requires_a_configured_supervisor_endpoint():
    planner = LangChainSupervisorPlanner(
        _settings(supervisor_agent_base_url=""), chain=_FakeChain(message=None)
    )

    with pytest.raises(ProviderUnavailableError):
        planner.choose({"observations": []}, timeout_seconds=5)


def test_planner_rejects_an_exhausted_turn_budget():
    planner = LangChainSupervisorPlanner(_settings(), chain=_FakeChain(message=None))

    with pytest.raises(ProviderInvalidOutputError):
        planner.choose({"observations": []}, timeout_seconds=0)


def test_planner_maps_a_tool_call_onto_the_command_contract():
    message = type(
        "Message",
        (),
        {"tool_calls": [{"name": "queue", "args": {}, "id": "c1"}]},
    )()
    chain = _FakeChain(message=message)
    planner = LangChainSupervisorPlanner(_settings(), chain=chain)

    command = planner.choose({"question": "what is late?", "observations": []}, timeout_seconds=5)

    assert command == {"action": "queue"}
    assert planner.provider_attempts == 1
    assert chain.payloads and "what is late?" in chain.payloads[0]["input"]


def test_planner_surfaces_an_answer_without_a_tool_call():
    chain = _FakeChain(message=type("Message", (), {"tool_calls": []})())
    planner = LangChainSupervisorPlanner(_settings(), chain=chain)

    with pytest.raises(ProviderInvalidOutputError):
        planner.choose({"observations": []}, timeout_seconds=5)


# -------------------------------------------------------- real LCEL assembly (needs the extra)

requires_langchain = pytest.mark.skipif(
    not langchain_llm.optional_dependencies_available(),
    reason="optional langchain extra is not installed (pip install -e 'backend[langchain]')",
)


def _fake_tool_model(reply: dict, recorded: list):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class FakeToolChatModel(BaseChatModel):
        bound_tools: list = []

        @property
        def _llm_type(self) -> str:
            return "fake-tool-chat"

        def bind_tools(self, tools, **kwargs):  # noqa: ANN001 - LangChain signature
            recorded.extend(tools)
            return self.model_copy(update={"bound_tools": list(tools)})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001
            message = AIMessage(
                content="",
                tool_calls=[{"name": reply["name"], "args": reply.get("args", {}), "id": "c1"}],
            )
            return ChatResult(generations=[ChatGeneration(message=message)])

    return FakeToolChatModel()


@requires_langchain
def test_tool_chain_binds_the_action_tools_through_lcel():
    from langchain_core.prompts import ChatPromptTemplate

    recorded: list = []
    model = _fake_tool_model({"name": "finish", "args": {"items": []}}, recorded)
    tools = LangChainSupervisorPlanner.action_tools()
    chain = langchain_llm.build_tool_chain(
        tools,
        model=model,
        prompt=ChatPromptTemplate.from_messages([("system", "plan"), ("human", "{input}")]),
    )

    message = chain.invoke({"input": "what is late?"})

    assert [tool.name for tool in recorded] == ["queue", "diagnose", "finish"]
    assert langchain_llm.tool_call_to_command(message) == {"action": "finish", "items": []}


@requires_langchain
def test_action_tools_describe_the_server_side_contract():
    tools = {tool.name: tool for tool in LangChainSupervisorPlanner.action_tools()}

    assert set(tools) == {"queue", "diagnose", "finish"}
    assert set(tools["diagnose"].args) == {"work_order_id", "version"}
    with pytest.raises(Exception):
        tools["queue"].func()


@requires_langchain
def test_planner_can_be_built_with_a_real_chat_model_and_chain_factory():
    captured: dict = {}

    def factory(*, timeout_seconds):
        captured["timeout"] = timeout_seconds
        return _FakeChain(message=type("Message", (), {"tool_calls": [{"name": "queue", "args": {}, "id": "c"}]})())

    planner = LangChainSupervisorPlanner(_settings(), chain_factory=factory)
    command = planner.choose({"observations": []}, timeout_seconds=4)

    assert command == {"action": "queue"}
    assert captured["timeout"] == 4


@requires_langchain
def test_langchain_engine_is_selected_when_the_extra_is_installed():
    recorder = _RecordingLogger()

    planner = build_supervisor_planner(_settings(supervisor_agent_engine="langchain"))

    assert type(planner) is LangChainSupervisorPlanner
    assert planner.mode == "langchain_real_model"
