# Agent interoperability

The harness `TeamManager` provides same-instance synchronous peer calls and background work. These calls are separate from [company group experts](../user/group-experts.md) and [remote instance bridges](../user/bridge.md).

Synchronous `ask_agent` returns the peer's result to the initiating tool call. Background requests enter the harness inbox and return a job identifier. The Octop `GlobalProcessor`, implementing `TeamProcessor`, delivers completion to the source conversation and its dashboard or IM surface.

Work for one callee is serialized; independent callees can run concurrently. Team hosts use asynchronous dispatch and receive completion callbacks, while member requests use synchronous peer calls. Inbox state and in-flight team coordination are process-local and must not be treated as the durable distributed execution queue.

See [peer calls](agent-call-agent.md), [background delegation](agent-delegation.md), [team behavior](../user/expert-teams.md) and the [shared execution contract](execution-contract.md).
