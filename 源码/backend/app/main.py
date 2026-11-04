from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import (
    BACKEND_PORT,
    CONTENT_ASSESSMENT_LLM_ENABLED,
    get_allowed_origins,
    setup_logging,
)
from app.core.task_manager import TaskManager
from app.core.topic_task_manager import TopicTaskManager
from app.services.deepseek_client import DeepSeekClient
from app.repositories.content_assessment_task_history import get_task_history_repo
from app.services.task_persistence import now_beijing_iso

setup_logging()

app = FastAPI(title="Innovation Assessment Agent API", version="0.1.0")

origins = get_allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    await get_task_history_repo().reconcile_interrupted(now_beijing_iso())
    app.state.task_manager = TaskManager()
    app.state.topic_task_manager = TopicTaskManager()
    # Keep model availability explicit at process start. The health record is
    # intentionally redacted and never contains an API key.
    if not CONTENT_ASSESSMENT_LLM_ENABLED:
        app.state.model_health = {
            "status": "not_configured",
            "profile": "",
            "model": "",
            "endpoint": "",
            "http_status": None,
            "error_code": "model_disabled",
            "error": "Model calls are disabled by CONTENT_ASSESSMENT_LLM_ENABLED.",
        }
    else:
        try:
            client = DeepSeekClient()
            app.state.model_health = await client.preflight()
            await client.aclose()
        except Exception as exc:
            app.state.model_health = {
                "status": "not_configured",
                "profile": "",
                "model": "",
                "endpoint": "",
                "http_status": None,
                "error_code": type(exc).__name__,
                "error": str(exc)[:240],
            }
    await app.state.topic_task_manager.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.task_manager.shutdown()
    await app.state.topic_task_manager.shutdown()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=BACKEND_PORT)
