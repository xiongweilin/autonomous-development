from fastapi import FastAPI

app = FastAPI(title="Autonomous Development Acceptance Target")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/answer")
def answer(value: str = "hello") -> dict[str, str]:
    return {"answer": value.strip().lower()}
