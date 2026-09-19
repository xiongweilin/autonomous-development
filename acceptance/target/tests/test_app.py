from fastapi.testclient import TestClient

from app import app


client = TestClient(app)


def test_answer_is_deterministic() -> None:
    response = client.get("/answer", params={"value": "  Hello  "})
    assert response.status_code == 200
    assert response.json() == {"answer": "hello"}
