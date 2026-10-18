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
