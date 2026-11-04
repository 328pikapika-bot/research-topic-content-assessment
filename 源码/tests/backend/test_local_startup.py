import pytest


@pytest.mark.asyncio
async def test_startup_skips_model_preflight_when_model_calls_are_disabled(monkeypatch):
    import app.main as main

    class TaskHistory:
        async def reconcile_interrupted(self, _now):
            return None

    class TaskManager:
        async def shutdown(self):
            return None

    class TopicTaskManager(TaskManager):
        async def start(self):
            return None

    class UnexpectedModelClient:
        def __init__(self):
            raise AssertionError("Model client must not be created when disabled")

    monkeypatch.setattr(main, "CONTENT_ASSESSMENT_LLM_ENABLED", False)
    monkeypatch.setattr(main, "get_task_history_repo", lambda: TaskHistory())
    monkeypatch.setattr(main, "TaskManager", TaskManager)
    monkeypatch.setattr(main, "TopicTaskManager", TopicTaskManager)
    monkeypatch.setattr(main, "DeepSeekClient", UnexpectedModelClient)

    await main.startup()

    assert main.app.state.model_health["status"] == "not_configured"
    assert main.app.state.model_health["error_code"] == "model_disabled"
