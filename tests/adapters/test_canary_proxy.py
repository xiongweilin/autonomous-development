import httpx
import pytest

from autonomous_development.adapters.canary_proxy import (
    CanaryMetricsRegistry,
    create_canary_proxy,
)
from autonomous_development.domain.models import RequestAttribution
from autonomous_development.ports.traffic import TrafficRouteSnapshot


class FakeRouteReader:
    def __init__(self, route: TrafficRouteSnapshot | None) -> None:
        self.route = route

    def read_current(self) -> TrafficRouteSnapshot | None:
        return self.route


class MemoryAttributions:
    def __init__(self) -> None:
        self.items: dict[str, RequestAttribution] = {}

    def add(self, attribution: RequestAttribution) -> RequestAttribution:
        existing = self.items.get(attribution.request_ref)
        if existing is not None and existing != attribution:
            raise ValueError("request attribution conflict")
        self.items[attribution.request_ref] = attribution
        return attribution

    def get(self, request_ref: str) -> RequestAttribution | None:
        return self.items.get(request_ref)


def route(weight: int = 10, *, with_attribution: bool = False) -> TrafficRouteSnapshot:
    return TrafficRouteSnapshot(
        experiment_id="experiment-1",
        stage_index=0,
        control_base_url="http://127.0.0.1:4100",
        candidate_base_url="http://127.0.0.1:4200",
        candidate_weight_percent=weight,
        operation_id="canary:experiment-1:stage:0",
        generation=1,
        evidence_ref="traffic:1",
        target_id="target-1" if with_attribution else None,
        control_release_id="release-1" if with_attribution else None,
        candidate_deployment_id="deployment-2" if with_attribution else None,
    )


@pytest.mark.asyncio
async def test_proxy_routes_weighted_sessions_and_records_metrics() -> None:
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
        for index in range(100):
            response = await client.get(
                "/work",
                headers={"x-autodev-session": f"session-{index}"},
            )
            assert response.status_code == 200
            if response.headers["x-autodev-arm"] == "candidate":
                candidate += 1
            else:
                control += 1

        snapshot = await client.get("/__autodev/metrics/1")

    assert candidate + control == 100
    assert candidate > 0
    assert control > candidate
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["candidate"]["requests"] == candidate
    assert payload["control"]["requests"] == control


@pytest.mark.asyncio
async def test_proxy_keeps_one_session_on_one_arm() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", request=request)

    app = create_canary_proxy(
        FakeRouteReader(route(50)),
        CanaryMetricsRegistry(),
        upstream_transport=httpx.MockTransport(upstream),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy.local",
    ) as client:
        arms = {
            (await client.get(
                "/work",
                headers={"x-autodev-session": "stable-session"},
            )).headers["x-autodev-arm"]
            for _ in range(20)
        }

    assert len(arms) == 1


@pytest.mark.asyncio
async def test_proxy_preserves_product_cookies_but_strips_proxy_session_cookie() -> None:
    observed_cookie: list[str] = []

    async def upstream(request: httpx.Request) -> httpx.Response:
        observed_cookie.append(request.headers.get("cookie", ""))
        return httpx.Response(200, content=b"ok", request=request)

    app = create_canary_proxy(
        FakeRouteReader(route(0)),
        CanaryMetricsRegistry(),
        upstream_transport=httpx.MockTransport(upstream),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy.local",
    ) as client:
        response = await client.get(
            "/work",
            headers={
                "cookie": "product_session=abc; autodev_session=route-cookie",
            },
        )

    assert response.status_code == 200
    assert observed_cookie == ["product_session=abc"]


@pytest.mark.asyncio
async def test_proxy_persists_candidate_request_attribution() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", request=request)

    attributions = MemoryAttributions()
    app = create_canary_proxy(
        FakeRouteReader(route(100, with_attribution=True)),
        CanaryMetricsRegistry(),
        attributions=attributions,
        upstream_transport=httpx.MockTransport(upstream),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://proxy.local",
    ) as client:
        response = await client.get(
            "/work",
            headers={"x-autodev-session": "candidate-session"},
        )

    assert response.status_code == 200
    request_ref = response.headers["x-autodev-request-ref"]
    attribution = attributions.get(request_ref)
    assert attribution is not None
    assert attribution.arm == "candidate"
    assert attribution.target_id == "target-1"
    assert attribution.release_id is None
    assert attribution.deployment_id == "deployment-2"
    assert attribution.experiment_id == "experiment-1"


@pytest.mark.asyncio
async def test_proxy_with_attribution_fails_closed_on_unidentified_route() -> None:
    app = create_canary_proxy(
        FakeRouteReader(route(100)),
        CanaryMetricsRegistry(),
        attributions=MemoryAttributions(),
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
