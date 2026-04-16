"""Minimal FastAPI service. The Builder agent will extend this."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict

app = FastAPI(title="items-service")

# In-memory store. Real service would use a database; this is fine for the demo.
_items: Dict[int, "Item"] = {}
_next_id: int = 1


class Item(BaseModel):
    id: int | None = None
    name: str
    price: float


class ItemCreate(BaseModel):
    name: str
    price: float


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/items", response_model=Item, status_code=201)
def create_item(payload: ItemCreate) -> Item:
    global _next_id
    item = Item(id=_next_id, name=payload.name, price=payload.price)
    _items[_next_id] = item
    _next_id += 1
    return item


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="Item not found")
    return _items[item_id]


@app.get("/items", response_model=list[Item])
def list_items() -> list[Item]:
    return list(_items.values())
