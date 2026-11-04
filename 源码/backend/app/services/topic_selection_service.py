"""智能选题服务门面。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agents.topic_selection.agent import TopicSelectionAgent
from app.agents.topic_selection.schemas import TopicSelectionState


class TopicSelectionService:
    def __init__(self) -> None:
        self.agent = TopicSelectionAgent()

    def create_initial_state(self, user_input: str, **kwargs: object) -> TopicSelectionState:
        return self.agent.create_initial_state(user_input, **kwargs)

    async def run(
        self,
        state: TopicSelectionState,
        *,
        on_stage_update: Callable[[TopicSelectionState], Awaitable[None]] | None = None,
    ) -> TopicSelectionState:
        return await self.agent.run(state, on_stage_update=on_stage_update)
