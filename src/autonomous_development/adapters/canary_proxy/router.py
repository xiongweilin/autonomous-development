from __future__ import annotations

import hashlib
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from autonomous_development.domain.models import RequestAttribution
from autonomous_development.ports.persistence import RequestAttributionRepository
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
_SESSION_COOKIE = "autodev_session"
_SESSION_HEADER = "x-autodev-session"


class _WeightedSelector:
    def choose(self, route: TrafficRouteSnapshot, session_id: str) -> Arm:
        weight = route.candidate_weight_percent
        if weight <= 0:
            return "control"
        if weight >= 100:
            return "candidate"
        digest = hashlib.sha256(
            f"{route.experiment_id}\0{session_id}".encode()
        ).digest()
        slot = int.from_bytes(digest[:8], "big") % 100
        return "candidate" if slot < weight else "control"


def create_canary_proxy(
    routes: TrafficRouteReader,
    metrics: CanaryMetricsRegistry,
    *,
    attributions: RequestAttributionRepository | None = None,
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

        session_id = _session_id(request)
        arm = selector.choose(route, session_id)
        request_ref = secrets.token_urlsafe(18)

        if attributions is not None:
            if (
                route.target_id is None
                or route.control_release_id is None
                or route.candidate_deployment_id is None
            ):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "active traffic route lacks attribution identity"},
                )
            try:
                attributions.add(
                    RequestAttribution(
                        request_ref=request_ref,
                        target_id=route.target_id,
                        observed_at=datetime.now(UTC),
                        arm=arm,
                        experiment_id=route.experiment_id,
                        release_id=(
                            route.control_release_id if arm == "control" else None
                        ),
                        deployment_id=(
                            route.candidate_deployment_id if arm == "candidate" else None
                        ),
                    )
                )
            except Exception:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "request attribution could not be persisted"},
                )

        base_url = route.candidate_base_url if arm == "candidate" else route.control_base_url
        target = base_url.rstrip("/") + request.url.path
        if request.url.query:
            target += "?" + request.url.query

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in _HOP_BY_HOP_HEADERS
            and key.lower() != _SESSION_HEADER
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
            response = Response(
                content=upstream.content,
                status_code=upstream.status_code,
                headers=response_headers,
            )
        except httpx.HTTPError:
            response = JSONResponse(
                status_code=502,
                content={"detail": "selected upstream unavailable"},
            )
        finally:
            latency_ms = (time.monotonic() - started) * 1000
            await metrics.record(
                route,
                arm,
                status_code=status_code,
                latency_ms=latency_ms,
            )

        response.headers["x-autodev-arm"] = arm
        response.headers["x-autodev-experiment"] = route.experiment_id
        response.headers["x-autodev-request-ref"] = request_ref
        response.headers[_SESSION_HEADER] = session_id
        response.set_cookie(
            _SESSION_COOKIE,
            session_id,
            httponly=True,
            samesite="strict",
        )
        return response

    return app


def _session_id(request: Request) -> str:
    supplied = request.headers.get(_SESSION_HEADER) or request.cookies.get(_SESSION_COOKIE)
    if supplied is not None:
        normalized = supplied.strip()
        if normalized and len(normalized) <= 128:
            return normalized
    return secrets.token_urlsafe(18)


def _require_loopback(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("V1 canary proxy only forwards to local HTTP loopback")
