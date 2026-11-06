import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from octop.infra.agents.middleware.history_roles import normalize_history_roles


def test_old_team_context_is_ordinary_input_and_system_prompt_stays_first():
    system = SystemMessage(content="actual expert instructions")
    old_dispatch = SystemMessage(content="old team assignment", id="dispatch")
    messages = [
        HumanMessage(content="count"),
        AIMessage(content="1"),
        old_dispatch,
        HumanMessage(content="count again"),
        ToolMessage(content="result", tool_call_id="x"),
    ]
    request = ModelRequest(
        model=ChatOpenAI(model="local", api_key="test"), system_message=system, messages=messages
    )
    normalized = normalize_history_roles(request)
    assert normalized.system_message is system
    assert [m.type for m in normalized.messages] == ["human", "ai", "human", "human", "tool"]
    assert normalized.messages[2].content == old_dispatch.content
    assert normalized.messages[2].id == "dispatch"
    assert request.messages[2] is old_dispatch
    assert normalize_history_roles(normalized) is normalized


@pytest.mark.parametrize("historical_system", [False, True])
@pytest.mark.parametrize("legacy_name", [False, True])
def test_internal_assistant_names_are_removed_only_from_model_request(
    historical_system, legacy_name
):
    model = ChatOpenAI(model="local", api_key="test", use_responses_api=False)
    original = AIMessage(
        content="agent_demo: quoted content must stay unchanged",
        name=None if legacy_name else "agent_demo",
        id="answer",
        additional_kwargs={"name": "agent_demo", "reasoning_content": "reasoning"},
        tool_calls=[{"id": "call1", "name": "lookup", "args": {"query": "example"}}],
    )
    before = original.model_dump()
    messages = [HumanMessage(content="continue"), original]
    if historical_system:
        messages.insert(0, SystemMessage(content="old dispatch"))
    request = ModelRequest(model=model, messages=messages)
    normalized = normalize_history_roles(request)
    outgoing = model._get_request_payload(normalized.messages)["messages"][-1]

    assert "name" not in outgoing
    assert outgoing["content"] == original.content
    assert outgoing["tool_calls"][0]["function"]["name"] == "lookup"
    assert normalized.messages[-1].id == "answer"
    assert normalized.messages[-1].additional_kwargs["reasoning_content"] == "reasoning"
    assert original.model_dump() == before
    assert normalize_history_roles(normalized) is normalized


def test_non_internal_names_and_multimodal_content_are_preserved():
    messages = [
        HumanMessage(content="question", name="agent_user"),
        AIMessage(content="answer", name="support", additional_kwargs={"name": "support"}),
        ToolMessage(content="result", name="agent_tool", tool_call_id="call1"),
        AIMessage(content=[{"type": "text", "text": "answer"}], name="agent_demo"),
    ]
    request = ModelRequest(model=ChatOpenAI(model="local", api_key="test"), messages=messages)
    normalized = normalize_history_roles(request)
    assert all(normalized.messages[i] is messages[i] for i in range(3))
    assert normalized.messages[3].name is None
    assert normalized.messages[3].content == messages[3].content
    assert messages[3].name == "agent_demo"
