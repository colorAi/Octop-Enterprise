from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from octop.infra.company_runtime.steer import CompanySteering


def submit(steering, turn, text="Use the corrected figures", pending="draft1", thread="thread"):
    return steering.send(
        {
            "thread_id": thread,
            "text": text,
            "expected_turn_id": turn,
            "client_pending_id": pending,
        }
    )


def test_steers_are_bound_to_a_turn_and_idempotent():
    steering = CompanySteering(lambda event: None)
    turn = steering.begin("thread")
    assert submit(steering, "stale")["reason"] == "expected_turn_id_mismatch"
    assert submit(steering, turn, thread="another")["reason"] == "no_active_turn"
    first = submit(steering, turn)
    assert first["type"] == "steer.queued"
    assert submit(steering, turn)["pending_id"] == first["pending_id"]
    assert len(steering.coordinator.get_state("thread").queue) == 1
    removed = steering.remove(
        {
            "thread_id": "thread",
            "turn_id": turn,
            "client_pending_id": "draft1",
            "pending_id": first["pending_id"],
        }
    )
    assert removed["status"] == "removed" and not removed["already_drained"]
    assert not steering.coordinator.has_pending("thread")


def test_cancelled_turn_cannot_drain_into_another_model_call():
    steering = CompanySteering(lambda event: None)
    turn = steering.begin("thread")
    submit(steering, turn)
    steering.cancel("thread")
    discarded = steering.coordinator.drain_for_continuation("thread")
    assert discarded.reason == "turn_cancelled"
    assert not steering.coordinator.has_pending("thread")


def test_cancel_discards_pending_inputs_without_replaying_them():
    events = []
    steering = CompanySteering(events.append)
    turn = steering.begin("thread")
    submit(steering, turn)
    steering.cancel("thread")
    assert submit(steering, turn, pending="draft2")["reason"] == "turn_already_cancelled"
    steering.end("thread", failed=False)
    discarded = next(e["frame"] for e in events if e["frame"]["type"] == "steer.discarded")
    assert discarded["reason"] == "turn_cancelled"
    assert discarded["client_pending_ids"] == ["draft1"]
    assert submit(steering, turn)["reason"] == "no_active_turn"


@pytest.mark.parametrize("with_tools", [True, False])
async def test_steer_drains_after_tools_or_after_complete_text(with_tools):
    events = []
    steering = CompanySteering(events.append)
    turn = steering.begin("thread")
    submit(steering, turn)
    persist = AsyncMock()

    class Agent:
        calls = []
        cancelled = False

        def cancel(self, thread):
            assert thread == "thread"
            self.cancelled = True

        async def stream(self, request):
            message = request["messages"][0]
            self.calls.append(message)
            if len(self.calls) == 1:
                if with_tools:
                    yield {"type": "tool_result", "content": "first tool completed"}
                    yield {"type": "tool_result", "content": "second tool completed"}
                yield {"type": "token", "content": "Original answer"}
                if not self.cancelled:
                    yield {"type": "token", "content": " finished"}
                yield {"type": "done"}
            else:
                assert message.content == "Use the corrected figures"
                yield {"type": "token", "content": "Corrected answer"}
                yield {"type": "done"}

    agent = Agent()
    chunks = [
        c async for c in steering.stream(agent, "thread", HumanMessage(content="original"), persist)
    ]
    assert agent.cancelled is with_tools
    assert len(agent.calls) == 2
    assert [c["content"] for c in chunks if c["type"] == "token"] == (
        ["Corrected answer"] if with_tools else ["Original answer", " finished", "Corrected answer"]
    )
    persist.assert_awaited_once_with(agent.calls[1])
    assert any(e["frame"]["type"] == "steer.drained" for e in events)
    assert submit(steering, turn, pending="too-late")["reason"] == "no_active_turn"


async def test_model_error_does_not_continue_a_queued_instruction():
    steering = CompanySteering(lambda event: None)
    turn = steering.begin("thread")
    submit(steering, turn)

    class Agent:
        async def stream(self, request):
            yield {"type": "error", "message": "model unavailable"}

    persist = AsyncMock()
    with pytest.raises(RuntimeError, match="model unavailable"):
        async for _ in steering.stream(
            Agent(), "thread", HumanMessage(content="original"), persist
        ):
            pass
    persist.assert_not_awaited()
