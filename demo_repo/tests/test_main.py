"""Baseline tests. Agent must not regress any of these."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_and_get_item():
    r = client.post("/items", json={"name": "widget", "price": 9.99})
    assert r.status_code == 201
    item = r.json()
    assert item["name"] == "widget"
    assert item["price"] == 9.99
    assert item["id"] is not None

    r = client.get(f"/items/{item['id']}")
    assert r.status_code == 200
    assert r.json() == item


def test_get_missing_item_returns_404():
    r = client.get("/items/999999")
    assert r.status_code == 404


def test_list_items():
    r = client.get("/items")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_delete_item_success():
    r = client.post("/items", json={"name": "temporary", "price": 5.00})
    assert r.status_code == 201
    item_id = r.json()["id"]

    r = client.delete(f"/items/{item_id}")
    assert r.status_code == 204
    assert r.content == b""

    r = client.get(f"/items/{item_id}")
    assert r.status_code == 404


def test_delete_missing_item_returns_404():
    r = client.delete("/items/999999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Item not found"
