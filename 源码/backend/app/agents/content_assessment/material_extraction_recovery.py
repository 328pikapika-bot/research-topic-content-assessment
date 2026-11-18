"""Bounded recovery of truncated extraction, with reusable grounded results."""
from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from .material_extraction import split_source_chunk
from .prompts import MATERIAL_SYSTEM_PROMPT, PROMPT_VERSION, material_extraction_prompt


RECOVERABLE_OUTPUT_ERRORS = frozenset({"ModelOutputTruncated", "JSONDecodeError"})
EXTRACTION_MAX_TOKENS = 6000
EXTRACTION_REPAIR_MAX_TOKENS = 9000
MAX_SPLIT_DEPTH = 3


def extraction_checkpoint(state, chunks: list[str]) -> dict:
    fingerprint = hashlib.sha256(
        (PROMPT_VERSION + "\0" + "\0".join(chunks)).encode("utf-8")
    ).hexdigest()
    checkpoint = state.output_json.get("material_extraction_checkpoint", {})
    if checkpoint.get("fingerprint") != fingerprint:
        checkpoint = {"fingerprint": fingerprint, "chunks": {}}
        state.output_json["material_extraction_checkpoint"] = checkpoint
    state.output_json.pop("material_extraction_failure", None)
    return checkpoint["chunks"]


async def extract_material_chunk(
    graph, state, chunk: str, checkpoint: dict, *, depth: int = 0,
    max_tokens: int = EXTRACTION_MAX_TOKENS,
) -> AsyncIterator[tuple[str, dict | None]]:
    """Yield only complete responses; the caller caches them after anchoring."""
    key = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    cached = checkpoint.get(key, {})
    if cached.get("status") == "completed":
        yield key, cached["payload"]
        return
    children = split_source_chunk(chunk) if depth < MAX_SPLIT_DEPTH else None
    if cached.get("status") != "split":
        previous = state.output_json.get("model_response_diagnostics", [])
        previous = previous[-1] if previous else None
        payload = await graph._request_model_json(
            state, stage="claim_extraction", system_prompt=MATERIAL_SYSTEM_PROMPT,
            user_prompt=material_extraction_prompt(chunk), max_tokens=max_tokens,
            recoverable_output=True,
        )
        if payload is not None:
            yield key, payload
            return
        diagnostics = state.output_json.get("model_response_diagnostics", [])
        diagnostic = diagnostics[-1] if diagnostics else {}
        error_type = diagnostic.get("error_type") if diagnostic is not previous else None
        if error_type not in RECOVERABLE_OUTPUT_ERRORS:
            yield key, None
            return
        events = state.output_json.setdefault("material_extraction_recoveries", [])
        action = "split" if children else "increase_output_budget"
        if not children and max_tokens >= EXTRACTION_REPAIR_MAX_TOKENS:
            action = "exhausted"
        events.append({"chunk_key": key, "depth": depth, "error_type": error_type,
                       "action": action, "max_tokens": max_tokens})
        del events[:-100]
        if action == "exhausted":
            state.output_json["material_extraction_failure"] = {
                "error_type": error_type, "chunk_key": key, "automatic_recovery_exhausted": True,
            }
            graph._model_unavailable = True
            yield key, None
            return
        if children:
            checkpoint[key] = {"status": "split"}
        await graph._emit_stage_update(state)
        if not children:
            async for result in extract_material_chunk(
                graph, state, chunk, checkpoint, depth=depth,
                max_tokens=EXTRACTION_REPAIR_MAX_TOKENS,
            ):
                yield result
            return
    # Successful child responses are applied and checkpointed before asking
    # for the next child. A retry reuses them and skips the failed parent call.
    for child in children or ():
        async for result in extract_material_chunk(graph, state, child, checkpoint, depth=depth + 1):
            yield result
        if graph._model_unavailable:
            return
