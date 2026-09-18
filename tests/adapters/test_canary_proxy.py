import httpx
import pytest

from autonomous_development.adapters.canary_proxy import (
    CanaryMetricsRegistry,
    create_canary_proxy,
)
from autonomous_development.ports.traffic import TrafficRouteSnapshot


class FakeRouteReader:
    def __init__(self, route: TrafficRouteSnapshot | None) -> None:
        self.route = route

    def read_current(self) -> TrafficRouteSnapshot | None:
        return self.route


def route(weight: int = 10) -> TrafficRouteSnapshot:
    return TrafficRouteSnapshot(
        experiment_id="experiment-1",
        stage_index=0,
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        candidate_weight_percent=weight,
        operation_id="canary:experiment-1:stage:0",
        generation=1,
        evidence_ref="traffic:1",
    )


@pytest.mark.asyncio
async def test_proxy_routes_exact_weight_over_full_cycle() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", request=request)

    metrics = CanaryMetricsRegistry()
    app = create_canary_proxy(
        FakeRouteReader(route(10)),
        metrics,
        upstream_transport=httpx.MockTransport(upstream),
    )
    transport = httpx.ASGITransport(app=app)
    candidate = 0
    control = 0
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.local") as client:
        for _ in range(100):
            response = await client.get("/work")
            assert response.status_code == 200
            if response.headers["x-autodev-arm"] == "candidate":
                candidate += 1
            else:
                control += 1

        snapshot = await client.get("/__autodev/metrics/1")

    assert (candidate, control) == (10, 90)
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["candidate"]["requests"] == 10
    assert payload["control"]["requests"] == 90


@pytest.mark.asyncio
async def test_proxy_without_route_fails_closed() -> None:
    app = create_canary_proxy(
        FakeRouteReader(None),
        CanaryMetricsRegistry(),
        upstream_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, request=request)
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy.local",
    ) as client:
        response = await client.get("/work")
    assert response.status_code == 503
