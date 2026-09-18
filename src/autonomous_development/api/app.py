from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from autonomous_development.application.feedback import FeedbackService
from autonomous_development.runtime.readiness import RuntimeReadinessService

from .feedback import create_feedback_router


def create_control_app(
    feedback: FeedbackService,
    readiness: RuntimeReadinessService,
) -> FastAPI:
    app = FastAPI(title="autonomous-development-control-plane")
    app.include_router(create_feedback_router(feedback))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> JSONResponse:
        report = readiness.check()
        content = {
            "status": "ready" if report.ready else "not-ready",
            "checks": [
                {
                    "name": check.name,
                    "ready": check.ready,
                    "detail": check.detail,
                }
                for check in report.checks
            ],
        }
        return JSONResponse(
            status_code=200 if report.ready else 503,
            content=content,
        )

    return app
