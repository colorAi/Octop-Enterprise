# Tool payloads for the model and UI

Plugin tools can return an envelope containing `octop_ui`, structured `data` and a text summary. The UI and model consume different parts of that result.

For oversized supported envelopes, `OctopUiOffloadMiddleware` moves structured data to `ToolMessage.artifact` and leaves the model a smaller envelope with `data_ref: "artifact"`. Artifacts remain available to history and UI serialization; model request converters serialize message content.

The dashboard resolves the artifact reference when rendering a supported tool card. Consumers must continue to handle older inline data, ordinary text results and file references. Backend serialization and dashboard support must be deployed together when changing this contract.

The current HTTP and WebSocket shapes are documented in the [API reference](api.md).
