from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.model_profiles import ModelProfile, resolve_active_model_profile

logger = logging.getLogger(__name__)


def retry_after_seconds(value):
    try:
        return max(0.0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class DeepSeekClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        metrics: "DeepSeekCallMetrics | None" = None,
    ) -> None:
        super().__init__(message)
        self.metrics = metrics


@dataclass
class DeepSeekCallMetrics:
    profile: str
    model: str
    stream: bool
    timeout_seconds: float
    max_tokens: int | None = None
    status_code: int | None = None
    header_ms: int | None = None
    first_line_ms: int | None = None
    first_reasoning_ms: int | None = None
    first_content_ms: int | None = None
    done_ms: int | None = None
    reasoning_chars: int = 0
    content_chars: int = 0
    chunks: int = 0
    timeout_kind: str | None = None
    error_type: str | None = None
    error: str | None = None
    finish_reason: str | None = None
    raw_content_preview: str = ""
    retry_after_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "model": self.model,
            "stream": self.stream,
            "timeout_seconds": self.timeout_seconds,
            "max_tokens": self.max_tokens,
            "status_code": self.status_code,
            "header_ms": self.header_ms,
            "first_line_ms": self.first_line_ms,
            "first_reasoning_ms": self.first_reasoning_ms,
            "first_content_ms": self.first_content_ms,
            "done_ms": self.done_ms,
            "reasoning_chars": self.reasoning_chars,
            "content_chars": self.content_chars,
            "chunks": self.chunks,
            "timeout_kind": self.timeout_kind,
            "error_type": self.error_type,
            "error": self.error,
            "finish_reason": self.finish_reason,
            "raw_content_preview": self.raw_content_preview,
            "retry_after_seconds": self.retry_after_seconds,
        }


@dataclass
class DeepSeekJSONResult:
    payload: dict[str, Any]
    metrics: DeepSeekCallMetrics


class DeepSeekClient:
    def __init__(self, model_profile: ModelProfile | None = None) -> None:
        self.model_profile = model_profile or resolve_active_model_profile()
        self.profile_name = self.model_profile.name
        self.api_key = self.model_profile.api_key.strip()
        self.base_url = self.model_profile.base_url.rstrip("/")
        self.model = self.model_profile.model
        self.timeout = float(self.model_profile.timeout)
        self.json_response_format = self.model_profile.json_response_format
        self.max_retries = max(0, int(os.getenv("CONTENT_ASSESSMENT_DEEPSEEK_MAX_RETRIES", "2")))
        self._client: httpx.AsyncClient | None = None

        if not self.api_key:
            raise DeepSeekClientError(
                f"Model profile `{self.profile_name}` has no API key configured."
            )

    async def preflight(self, *, timeout_seconds: float = 8.0) -> dict[str, Any]:
        """Run a minimal authenticated request and return redacted health metadata."""
        started = time.perf_counter()
        try:
            result = await self.generate_json_with_metrics(
                system_prompt="只输出 JSON。",
                user_prompt='{"health_check":true}',
                timeout_seconds=timeout_seconds,
                max_retries=0,
                max_tokens=64,
            )
            return {
                "status": "available",
                "profile": self.profile_name,
                "model": self.model,
                "endpoint": self.base_url,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "http_status": result.metrics.status_code,
                "error": "",
            }
        except Exception as exc:
            metrics = exc.metrics if isinstance(exc, DeepSeekClientError) else None
            return {
                "status": "unavailable",
                "profile": self.profile_name,
                "model": self.model,
                "endpoint": self.base_url,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "http_status": metrics.status_code if metrics else None,
                "error_code": metrics.error_type if metrics else type(exc).__name__,
                "error": str(exc)[:240],
            }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        first_chunk_timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        result = await self.generate_json_with_metrics(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_tokens=max_tokens,
            stream=stream,
            first_chunk_timeout_seconds=first_chunk_timeout_seconds,
        )
        return result.payload

    async def generate_json_with_metrics(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        first_chunk_timeout_seconds: float | None = None,
    ) -> DeepSeekJSONResult:
        logger.info(
            "DeepSeek request profile=%s model=%s prompt_len=%d stream=%s max_tokens=%s",
            self.profile_name,
            self.model,
            len(user_prompt),
            stream,
            max_tokens,
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            # Structured extraction benefits from a low, stable temperature.
            # Keep the override operationally configurable for model profiles.
            "temperature": min(0.3, max(0.0, float(os.getenv("CONTENT_ASSESSMENT_LLM_TEMPERATURE", "0.1")))),
        }
        if self.json_response_format:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        if stream:
            content, metrics = await self._post_stream(
                "/chat/completions",
                payload,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                first_chunk_timeout_seconds=first_chunk_timeout_seconds,
            )
        else:
            response, metrics = await self._post(
                "/chat/completions",
                payload,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
            try:
                choice = response["choices"][0]
                content = choice["message"]["content"]
                metrics.finish_reason = choice.get("finish_reason") or choice.get("stop_reason")
            except (KeyError, IndexError, TypeError) as exc:
                metrics.error_type = type(exc).__name__
                metrics.error = "DeepSeek 返回格式不符合预期。"
                raise DeepSeekClientError(
                    "DeepSeek 返回格式不符合预期。",
                    metrics=metrics,
                ) from exc
            metrics.content_chars = len(content or "")
        metrics.raw_content_preview = self._preview_content(content)
        if metrics.finish_reason == "length":
            metrics.error_type = "ModelOutputTruncated"
            metrics.error = "模型输出达到长度上限，需补齐完整结构。"
            raise DeepSeekClientError(metrics.error, metrics=metrics)
        try:
            return DeepSeekJSONResult(
                payload=self._parse_json_content(content),
                metrics=metrics,
            )
        except json.JSONDecodeError as exc:
            metrics.error_type = type(exc).__name__
            metrics.error = "DeepSeek 未返回合法 JSON。"
            raise DeepSeekClientError("DeepSeek 未返回合法 JSON。", metrics=metrics) from exc

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> tuple[dict[str, Any], DeepSeekCallMetrics]:
        client = await self._get_client()
        last_error: Exception | None = None
        timeout_value = max(1.0, float(timeout_seconds or self.timeout))
        request_timeout = httpx.Timeout(timeout_value)
        request_max_retries = max(0, int(self.max_retries if max_retries is None else max_retries))
        for attempt in range(request_max_retries + 1):
            metrics = DeepSeekCallMetrics(
                profile=self.profile_name,
                model=self.model,
                stream=False,
                timeout_seconds=timeout_value,
                max_tokens=payload.get("max_tokens"),
            )
            started_at = time.perf_counter()
            try:
                resp = await client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=request_timeout,
                )
                metrics.status_code = resp.status_code
                metrics.retry_after_seconds = retry_after_seconds(getattr(resp, 'headers', {}).get('Retry-After'))
                metrics.done_ms = self._elapsed_ms(started_at)
                if resp.status_code in (401, 403):
                    metrics.error_type = "HTTPAuthorizationError"
                    metrics.error = f"HTTP {resp.status_code}"
                    raise DeepSeekClientError(
                        f"DeepSeek 授权失败，请检查模型预设 `{self.profile_name}` 的 API Key 或模型权限。",
                        metrics=metrics,
                    )
                if resp.status_code == 402:
                    metrics.error_type = "HTTPPaymentRequired"
                    metrics.error = "HTTP 402"
                    raise DeepSeekClientError(
                        f"DeepSeek 账户余额不足，请检查模型预设 `{self.profile_name}`。",
                        metrics=metrics,
                    )
                if resp.status_code == 200:
                    return resp.json(), metrics
                if resp.status_code not in {408, 429, 500, 502, 503, 504}:
                    detail = resp.text[:300]
                    metrics.error_type = "HTTPError"
                    metrics.error = f"HTTP {resp.status_code}: {detail[:120]}"
                    raise DeepSeekClientError(
                        f"DeepSeek 请求失败：HTTP {resp.status_code}。{detail}",
                        metrics=metrics,
                    )
                if attempt >= request_max_retries:
                    detail = resp.text[:300]
                    metrics.error_type = "HTTPRetriableError"
                    metrics.error = f"HTTP {resp.status_code}: {detail[:120]}"
                    logger.error(
                        "DeepSeek HTTP %d after %d retries for profile=%s: %s",
                        resp.status_code,
                        attempt,
                        self.profile_name,
                        detail[:200],
                    )
                    raise DeepSeekClientError(
                        f"DeepSeek 请求失败：HTTP {resp.status_code}。{detail}",
                        metrics=metrics,
                    )
                last_error = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                await self._sleep_before_retry(attempt, f"HTTP {resp.status_code}")
            except (
                httpx.TimeoutException,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as exc:
                last_error = exc
                metrics.done_ms = self._elapsed_ms(started_at)
                metrics.error_type = type(exc).__name__
                metrics.error = str(exc)[:200]
                if attempt >= request_max_retries:
                    if isinstance(exc, httpx.TimeoutException):
                        metrics.timeout_kind = "total"
                        logger.error(
                            "DeepSeek timeout after %d retries for profile=%s: %s: %s",
                            attempt,
                            self.profile_name,
                            type(exc).__name__,
                            exc,
                        )
                        raise DeepSeekClientError(
                            f"DeepSeek 服务响应超时（{type(exc).__name__}），"
                            f"模型预设 `{self.profile_name}` 已自动重试 {attempt} 次但仍未成功。",
                            metrics=metrics,
                        ) from exc
                    logger.error(
                        "DeepSeek network error after %d retries for profile=%s: %s: %s",
                        attempt,
                        self.profile_name,
                        type(exc).__name__,
                        exc,
                    )
                    raise DeepSeekClientError(
                        f"DeepSeek 服务连接不稳定（{type(exc).__name__}），"
                        f"模型预设 `{self.profile_name}` 已自动重试 {attempt} 次但仍未成功。",
                        metrics=metrics,
                    ) from exc
                logger.warning(
                    "DeepSeek transient error for profile=%s (%s), retry #%d: %s",
                    self.profile_name,
                    type(exc).__name__,
                    attempt + 1,
                    exc,
                )
                await self._sleep_before_retry(attempt, f"{type(exc).__name__}: {exc}")
        else:
            raise DeepSeekClientError("DeepSeek 服务暂时不可用，请稍后重试。") from last_error

    async def _post_stream(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        first_chunk_timeout_seconds: float | None = None,
    ) -> tuple[str, DeepSeekCallMetrics]:
        client = await self._get_client()
        stream_payload = {**payload, "stream": True}
        timeout_value = max(1.0, float(timeout_seconds or self.timeout))
        first_chunk_timeout = max(
            1.0,
            float(first_chunk_timeout_seconds or min(60.0, timeout_value)),
        )
        request_timeout = httpx.Timeout(timeout_value)
        request_max_retries = max(0, int(self.max_retries if max_retries is None else max_retries))
        last_error: Exception | None = None
        for attempt in range(request_max_retries + 1):
            metrics = DeepSeekCallMetrics(
                profile=self.profile_name,
                model=self.model,
                stream=True,
                timeout_seconds=timeout_value,
                max_tokens=stream_payload.get("max_tokens"),
            )
            started_at = time.perf_counter()
            first_chunk_deadline = started_at + first_chunk_timeout
            content_chunks: list[str] = []
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}{path}",
                    json=stream_payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=request_timeout,
                ) as resp:
                    metrics.status_code = resp.status_code
                    metrics.retry_after_seconds = retry_after_seconds(getattr(resp, 'headers', {}).get('Retry-After'))
                    metrics.header_ms = self._elapsed_ms(started_at)
                    if resp.status_code in (401, 403):
                        metrics.error_type = "HTTPAuthorizationError"
                        metrics.error = f"HTTP {resp.status_code}"
                        raise DeepSeekClientError(
                            f"DeepSeek 授权失败，请检查模型预设 `{self.profile_name}` 的 API Key 或模型权限。",
                            metrics=metrics,
                        )
                    if resp.status_code == 402:
                        metrics.error_type = "HTTPPaymentRequired"
                        metrics.error = "HTTP 402"
                        raise DeepSeekClientError(
                            f"DeepSeek 账户余额不足，请检查模型预设 `{self.profile_name}`。",
                            metrics=metrics,
                        )
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                        metrics.error_type = "HTTPError"
                        metrics.error = f"HTTP {resp.status_code}: {detail[:120]}"
                        raise DeepSeekClientError(
                            f"DeepSeek 请求失败：HTTP {resp.status_code}。{detail}",
                            metrics=metrics,
                        )

                    line_iter = resp.aiter_lines()
                    while True:
                        try:
                            if metrics.first_line_ms is None:
                                remaining = first_chunk_deadline - time.perf_counter()
                                if remaining <= 0:
                                    raise asyncio.TimeoutError
                                line = await asyncio.wait_for(line_iter.__anext__(), timeout=remaining)
                            else:
                                line = await line_iter.__anext__()
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError as exc:
                            metrics.timeout_kind = "first_chunk"
                            metrics.error_type = type(exc).__name__
                            metrics.error = "server_queue_or_first_token_blocked"
                            raise DeepSeekClientError(
                                f"DeepSeek 流式响应超过 {first_chunk_timeout:.0f} 秒仍未返回首个 chunk，"
                                "疑似服务排队或首 token 阻塞。",
                                metrics=metrics,
                            ) from exc
                        if not line:
                            continue
                        now_ms = self._elapsed_ms(started_at)
                        if metrics.first_line_ms is None:
                            metrics.first_line_ms = now_ms
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            metrics.done_ms = now_ms
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        metrics.chunks += 1
                        choice = (chunk.get("choices") or [{}])[0]
                        metrics.finish_reason = choice.get("finish_reason") or metrics.finish_reason
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or ""
                        content = delta.get("content") or ""
                        if reasoning:
                            if metrics.first_reasoning_ms is None:
                                metrics.first_reasoning_ms = now_ms
                            metrics.reasoning_chars += len(reasoning)
                        if content:
                            if metrics.first_content_ms is None:
                                metrics.first_content_ms = now_ms
                            metrics.content_chars += len(content)
                            content_chunks.append(content)
                if metrics.done_ms is None:
                    metrics.done_ms = self._elapsed_ms(started_at)
                content = self._strip_think_content("".join(content_chunks))
                metrics.raw_content_preview = self._preview_content(content)
                return content, metrics
            except (
                httpx.TimeoutException,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                DeepSeekClientError,
            ) as exc:
                last_error = exc
                metrics.done_ms = self._elapsed_ms(started_at)
                if not metrics.error_type:
                    metrics.error_type = type(exc).__name__
                    metrics.error = str(exc)[:200]
                if isinstance(exc, DeepSeekClientError):
                    raise
                if isinstance(exc, httpx.TimeoutException) and not metrics.timeout_kind:
                    metrics.timeout_kind = "total"
                if attempt >= request_max_retries:
                    if isinstance(exc, httpx.TimeoutException):
                        logger.error(
                            "DeepSeek stream timeout after %d retries for profile=%s: %s: %s",
                            attempt,
                            self.profile_name,
                            type(exc).__name__,
                            exc,
                        )
                        raise DeepSeekClientError(
                            f"DeepSeek 流式服务响应超时（{type(exc).__name__}），"
                            f"模型预设 `{self.profile_name}` 已自动重试 {attempt} 次但仍未成功。"
                        ) from exc
                    logger.error(
                        "DeepSeek stream network error after %d retries for profile=%s: %s: %s",
                        attempt,
                        self.profile_name,
                        type(exc).__name__,
                        exc,
                    )
                    raise DeepSeekClientError(
                        f"DeepSeek 流式服务连接不稳定（{type(exc).__name__}），"
                        f"模型预设 `{self.profile_name}` 已自动重试 {attempt} 次但仍未成功。"
                    ) from exc
                await self._sleep_before_retry(attempt, f"{type(exc).__name__}: {exc}")
        raise DeepSeekClientError("DeepSeek 流式服务暂时不可用，请稍后重试。") from last_error

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return round((time.perf_counter() - started_at) * 1000)

    @staticmethod
    def _strip_think_content(content: str) -> str:
        if "</think>" in content:
            return content.split("</think>", 1)[-1].strip()
        if "<think>" in content:
            return content.split("<think>", 1)[0].strip()
        return content.strip()

    @staticmethod
    def _preview_content(content: str | None, limit: int = 800) -> str:
        if not content:
            return ""
        return content[:limit]

    async def _sleep_before_retry(self, attempt: int, reason: str) -> None:
        delay = min(10.0, 2.0 * (2 ** attempt))
        logger.warning(
            "DeepSeek 调用波动，profile=%s，%.1f 秒后重试：%s",
            self.profile_name,
            delay,
            reason,
        )
        await asyncio.sleep(delay)

    @staticmethod
    def _strip_json_fence(content: str) -> str:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = (
                cleaned.removeprefix("```json")
                .removeprefix("```JSON")
                .removeprefix("```")
                .strip()
            )
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        return cleaned

    def _try_repair_truncated_json(self, content: str) -> dict | None:
        cleaned = self._strip_json_fence(content)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        open_braces = 0
        open_brackets = 0
        in_string = False
        escape_next = False
        for ch in cleaned:
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                open_braces += 1
            elif ch == "}":
                open_braces -= 1
            elif ch == "[":
                open_brackets += 1
            elif ch == "]":
                open_brackets -= 1
        if open_braces > 0 or open_brackets > 0:
            if in_string:
                cleaned += '"'
            cleaned += "]" * open_brackets + "}" * open_braces
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
        return None

    def _iter_json_objects(self, content: str) -> list[dict[str, Any]]:
        decoder = json.JSONDecoder()
        candidates: list[tuple[int, dict[str, Any]]] = []
        for match in re.finditer(r"[\{\[]", content):
            try:
                value, end = decoder.raw_decode(content[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append((end, value))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [value for _, value in candidates]

    def _parse_json_content(self, content: str) -> dict[str, Any]:
        cleaned = self._strip_json_fence(content)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            for fenced in re.findall(r"```(?:json|JSON)?\s*(.*?)```", content, flags=re.DOTALL):
                try:
                    parsed = json.loads(fenced.strip())
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
            # Only accept a complete outer object. Scanning every opening brace
            # silently mistakes a complete inner item for a truncated response.
            start = cleaned.find("{")
            candidates = []
            while start >= 0:
                # Move past the entire outer object; never scan its children.
                parsed, end = json.JSONDecoder().raw_decode(cleaned, start)
                if isinstance(parsed, dict):
                    candidates.append((end - start, parsed))
                start = cleaned.find("{", end)
            if candidates:
                return max(candidates, key=lambda item: item[0])[1]
            raise
