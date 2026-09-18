from fastapi import FastAPI
from fastapi.testclient import TestClient

from autonomous_development.api.app import create_control_app
from autonomous_development.ports.readiness import ReadinessCheck, ReadinessReport


class Feedback:
    def ingest(self, submission):
        raise AssertionError("feedback ingestion is not exercised by control endpoint tests")


class Readiness:
    def __init__(self, ready: bool) -> None:
        self.ready = ready

    def check(self) -> ReadinessReport:
        return ReadinessReport(
            ready=self.ready,
            checks=(
                ReadinessCheck(
                    name="database",
                    ready=self.ready,
                    detail="reachable" if self.ready else "down",
                ),
            ),
        )


def test_control_app_health_ready_and_product_mount() -> None:
    product = FastAPI()

    @product.get("/ping")
    def ping() -> dict[str, str]:
        return {"product": "ok"}

    client = TestClient(
        create_control_app(
            Feedback(),  # type: ignore[arg-type]
            Readiness(True),
            product_app=product,
            product_mount_path="/product",
        )
    )

    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert client.get("/product/ping").json() == {"product": "ok"}


def test_control_app_readiness_fails_closed_and_mount_must_be_absolute() -> None:
    client = TestClient(
        create_control_app(
            Feedback(),  # type: ignore[arg-type]
            Readiness(False),
        )
    )
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not-ready",
        "checks": [{"name": "database", "ready": False, "detail": "down"}],
    }

    try:
        create_control_app(
            Feedback(),  # type: ignore[arg-type]
            Readiness(True),
            product_mount_path="relative",
        )
    except ValueError as exc:
        assert "absolute" in str(exc)
    else:
        raise AssertionError("relative product mount must be rejected")
