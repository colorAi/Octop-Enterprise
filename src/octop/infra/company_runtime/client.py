"""Native-only company transport; no credentials in URLs, logs or projects."""

from __future__ import annotations

import asyncio
import base64
import ssl
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessageChunk, messages_from_dict, messages_to_dict
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from pydantic import ConfigDict, Field

from octop.infra.company_runtime.model_context import calibrated_prompt_tokens


class CompanyServiceError(RuntimeError):
    def __init__(self, reason: str, status: int) -> None:
        super().__init__(reason)
        self.status = status


class CompanyModelError(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.retryable = retryable


class CompanyClient:
    def __init__(self, base: str, credential: str, *, transport: Any = None) -> None:
        url = urlsplit(base)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("invalid_company_origin")
        tls_context: ssl.SSLContext
        try:
            import truststore

            tls_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            tls_context = ssl.create_default_context()
        self.http = httpx.AsyncClient(
            base_url=base.rstrip("/") + "/api/company-runtime/",
            headers={"X-Octop-Device-Credential": credential},
            timeout=httpx.Timeout(120, connect=10),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            verify=tls_context,
        )

    async def request(self, path: str, body: dict[str, Any] | None = None) -> Any:
        timeout = (
            httpx.Timeout(900, connect=10)
            if path.endswith(("/images", "/videos", "/speech"))
            else self.http.timeout
        )
        for attempt in range(3):
            try:
                response = await self.http.request(
                    "GET" if body is None else "POST", path, json=body, timeout=timeout
                )
                break
            except httpx.TransportError:
                if body is not None or attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))
        if response.is_error:
            reason = f"company_service_{response.status_code}"
            try:
                data = response.json()
                error = data.get("error") or {}
                reason = (error.get("details") or {}).get("reason") or error.get("code") or reason
            except (ValueError, AttributeError):
                pass
            raise CompanyServiceError(reason, response.status_code)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self.http.aclose()


class CompanyChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: Any = Field(exclude=True)
    task_id: str
    model_ref: str

    @property
    def _llm_type(self) -> str:
        return "octop-company"

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Company runtime requires asynchronous model calls")

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])

    async def _astream(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        limit = (self.profile or {}).get("max_input_tokens", 128_000)
        calibrated = calibrated_prompt_tokens(messages, kwargs.get("tools", []))
        if calibrated is not None and calibrated >= int(limit * 0.85):
            # The outer compaction middleware archives/summarizes and performs
            # one smaller retry. No provider request or tool has run here.
            raise ContextOverflowError("company_model_context_pressure")
        body = {
            "model": self.model_ref,
            "messages": messages_to_dict(messages),
            "tools": kwargs.get("tools", []),
        }
        received = False
        generated = False
        finish_reason = None
        input_tokens = 0
        try:
            async with self.client.http.stream(
                "POST",
                f"tasks/{self.task_id}/model",
                json=body,
                timeout=httpx.Timeout(600, connect=10),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    import json

                    value = json.loads(line)
                    if "error" in value:
                        if value["error"] == "company_model_context_overflow" and not received:
                            raise ContextOverflowError("company_model_context_overflow")
                        raise CompanyModelError(
                            value["error"], retryable=bool(value.get("retryable")) and not received
                        )
                    received = True
                    message = cast(BaseMessageChunk, messages_from_dict([value])[0])
                    generated = (
                        generated
                        or bool(message.content)
                        or bool(getattr(message, "tool_call_chunks", None))
                    )
                    finish_reason = message.response_metadata.get("finish_reason") or finish_reason
                    input_tokens = (getattr(message, "usage_metadata", None) or {}).get(
                        "input_tokens", input_tokens
                    )
                    yield ChatGenerationChunk(message=message)
            if finish_reason in {"length", "max_tokens"}:
                if not generated and input_tokens >= int(limit * 0.85):
                    # Reasoning-only output may exhaust the remaining context
                    # without an HTTP error. Do not accept it as task completion.
                    raise ContextOverflowError("company_model_context_exhausted")
                # Partial text/tool arguments cannot safely be replayed silently.
                raise CompanyModelError("company_model_output_truncated")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise CompanyModelError(
                f"company_model_http_{status}",
                retryable=not received and (status in {408, 429} or status >= 500),
            ) from exc
        except httpx.TransportError as exc:
            raise CompanyModelError(
                "company_model_timeout"
                if isinstance(exc, httpx.TimeoutException)
                else "company_model_connection_lost",
                retryable=not received,
            ) from exc

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        combined = None
        async for chunk in self._astream(messages, stop, run_manager, **kwargs):
            combined = chunk if combined is None else combined + chunk
        if combined is None:
            raise CompanyModelError("company_model_empty_response", retryable=True)
        from langchain_core.messages import message_chunk_to_message

        return ChatResult(
            generations=[ChatGeneration(message=message_chunk_to_message(combined.message))]
        )


class RemoteCheckpointSaver(BaseCheckpointSaver[int]):
    """All official graph state is remote; serialization never enables pickle."""

    def __init__(self, client: CompanyClient, task_id: str, thread_id: str) -> None:
        super().__init__()
        self.client, self.task_id, self.thread_id = client, task_id, thread_id

    def pack(self, value: Any) -> list[str]:
        kind, data = self.serde.dumps_typed(value)
        if kind == "pickle":
            raise ValueError("pickle_checkpoint_forbidden")
        return [kind, base64.b64encode(data).decode()]

    def unpack(self, value: list[str]) -> Any:
        if value[0] == "pickle":
            raise ValueError("pickle_checkpoint_forbidden")
        return self.serde.loads_typed((value[0], base64.b64decode(value[1], validate=True)))

    def scope(self, config: Any) -> dict[str, Any]:
        cfg = config["configurable"]
        if cfg["thread_id"] != self.thread_id:
            raise ValueError("checkpoint_thread_mismatch")
        return {
            "namespace": cfg.get("checkpoint_ns", ""),
            "checkpoint_id": cfg.get("checkpoint_id"),
        }

    async def aget_tuple(self, config: Any) -> Any:
        value = await self.client.request(
            f"tasks/{self.task_id}/checkpoint/get", self.scope(config)
        )
        if value is None:
            return None
        return CheckpointTuple(
            config=value["config"],
            checkpoint=self.unpack(value["checkpoint"]),
            metadata=self.unpack(value["metadata"]),
            parent_config=value.get("parent_config"),
            pending_writes=[tuple(self.unpack(w)) for w in value["pending_writes"]],
        )

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        scope = self.scope(config)
        result = {
            "configurable": {
                "thread_id": self.thread_id,
                "checkpoint_ns": scope["namespace"],
                "checkpoint_id": checkpoint["id"],
            }
        }
        payload = {
            "config": result,
            "checkpoint": self.pack(checkpoint),
            "metadata": self.pack(metadata),
            "parent_config": {
                "configurable": {
                    "thread_id": self.thread_id,
                    "checkpoint_ns": scope["namespace"],
                    "checkpoint_id": scope["checkpoint_id"],
                }
            }
            if scope["checkpoint_id"]
            else None,
        }
        await self.client.request(
            f"tasks/{self.task_id}/checkpoint/put",
            {
                "namespace": scope["namespace"],
                "checkpoint_id": checkpoint["id"],
                "payload": payload,
            },
        )
        return result

    async def aput_writes(
        self, config: Any, writes: Any, task_id: str, task_path: str = ""
    ) -> None:
        await self.client.request(
            f"tasks/{self.task_id}/checkpoint/writes",
            {
                **self.scope(config),
                "task_key": task_id,
                "writes": [
                    {
                        "idx": WRITES_IDX_MAP.get(channel, i),
                        "value": self.pack([task_id, channel, value]),
                    }
                    for i, (channel, value) in enumerate(writes)
                ],
            },
        )

    async def alist(
        self, config: Any, *, filter: Any = None, before: Any = None, limit: Any = None
    ) -> AsyncIterator[Any]:
        # Follow parent links, bounded by the caller. No unrelated threads exposed.
        current = config
        count = 0
        while current and (limit is None or count < limit):
            item = await self.aget_tuple(current)
            if item is None:
                break
            if (
                not before
                or item.config["configurable"]["checkpoint_id"]
                < before["configurable"]["checkpoint_id"]
            ) and (not filter or all(item.metadata.get(k) == v for k, v in filter.items())):
                yield item
                count += 1
            current = item.parent_config
