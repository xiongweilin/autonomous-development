from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from autonomous_development.ports.traffic import TrafficRouteReader, TrafficRouteSnapshot

from .metrics import Arm, CanaryMetricsRegistry

_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)


class _WeightedSelector:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._counters: dict[int, int] = {}

    async def choose(self, route: TrafficRouteSnapshot) -> Arm:
        weight = route.candidate_weight_percent
        if weight <= 0:
            return "control"
        if weight >= 100:
            return "candidate"
        async with self._lock:
            counter = self._counters.get(route.generation, 0)
            self._counters[route.generation] = counter + 1
            if len(self._counters) > 128:
                oldest = sorted(self._counters)[:-64]
                for generation in oldest:
                    self._counters.pop(generation, None)
        slot = (counter * 37) % 100
        return "candidate" if slot < weight else "control"


def create_canary_proxy(
    routes: TrafficRouteReader,
    metrics: CanaryMetricsRegistry,
    *,
    upstream_timeout_seconds: float = 30.0,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    if upstream_timeout_seconds <= 0:
        raise ValueError("upstream timeout must be positive")

    app = FastAPI(title="autonomous-development-canary-proxy")
    selector = _WeightedSelector()

    @app.get("/__autodev/metrics/{generation}")
    async def generation_metrics(generation: int) -> Response:
        snapshot = await metrics.snapshot(generation)
        if snapshot is None:
            return JSONResponse(status_code=404, content={"detail": "generation not observed"})
        return JSONResponse(content=snapshot)

    @app.api_route(
        "/__autodev/{reserved_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def reserved(reserved_path: str) -> Response:
        del reserved_path
        return JSONResponse(status_code=404, content={"detail": "reserved management path"})

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def proxy(request: Request, path: str) -> Response:
        del path
        route = routes.read_current()
        if route is None:
            return JSONResponse(status_code=503, content={"detail": "no active traffic route"})
        _require_loopback(route.control_base_url)
        _require_loopback(route.candidate_base_url)

        arm = await selector.choose(route)
        base_url = route.candidate_base_url if arm == "candidate" else route.control_base_url
        target = base_url.rstrip("/") + request.url.path
        if request.url.query:
            target += "?" + request.url.query

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
        }
        body = await request.body()
        started = time.monotonic()
        status_code = 502
        try:
            async with httpx.AsyncClient(
                timeout=upstream_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                transport=upstream_transport,
            ) as client:
                upstream = await client.request(
                    request.method,
                    target,
                    headers=headers,
                    content=body,
                )
            status_code = upstream.status_code
            response_headers = {
                key: value
                for key, value in upstream.headers.items()
                if key.lower() not in _HOP_BY_HOP_HEADERS
            }
            response_headers["x-autodev-arm"] = arm
            response_headers["x-autodev-experiment"] = route.experiment_id
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
            )
        except httpx.HTTPError:
            return JSONResponse(
                status_code=502,
                content={"detail": "selected upstream unavailable"},
                headers={
                    "x-autodev-arm": arm,
                    "x-autodev-experiment": route.experiment_id,
                },
            )
        finally:
            latency_ms = (time.monotonic() - started) * 1000
            await metrics.record(
                route,
                arm,
                status_code=status_code,
                latency_ms=latency_ms,
            )

    return app


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 canary proxy only forwards to local HTTP loopback")
