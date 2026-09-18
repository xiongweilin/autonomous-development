from __future__ import annotations

import argparse
import json

import uvicorn

from autonomous_development.runtime.composition import compose_runtime
from autonomous_development.runtime.config import RuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="autonomous-development")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve", help="run the local V1 control plane")
    subcommands.add_parser("ready", help="run live local readiness probes")
    arguments = parser.parse_args()

    settings = RuntimeSettings.from_environment()
    runtime = compose_runtime(settings)
    if arguments.command == "ready":
        try:
            report = runtime.readiness.check()
            print(
                json.dumps(
                    {
                        "ready": report.ready,
                        "checks": [
                            {
                                "name": check.name,
                                "ready": check.ready,
                                "detail": check.detail,
                            }
                            for check in report.checks
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            raise SystemExit(0 if report.ready else 1)
        finally:
            runtime.close()

    runtime.launch()
    try:
        uvicorn.run(
            runtime.app,
            host=settings.api_host,
            port=settings.api_port,
            log_level="info",
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
