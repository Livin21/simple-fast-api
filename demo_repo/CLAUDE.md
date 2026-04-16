# Repo conventions (CLAUDE.md)

This file tells the Builder agent how code in this repo is written.

## Project layout

- `app/main.py` holds the FastAPI app and all routes
- `tests/` mirrors `app/` — every module gets a `test_*.py` with the same name
- In-memory state lives at module scope in `main.py` and is prefixed with `_`

## Conventions

- Use Pydantic models for all request/response shapes. Keep `ItemCreate`-style input models separate from the full `Item` response model.
- Raise `HTTPException` with a clear `detail` message rather than returning error dicts.
- HTTP status codes go on the route decorator (`status_code=201`), not on the return.
- Type-hint everything. `list[X]` and `dict[X, Y]` over `List[X]` / `Dict[X, Y]`.
- No print statements. No TODO comments left in committed code.

## Testing

- Use `TestClient` from `fastapi.testclient`.
- Every new endpoint gets at least: happy path, 404 / validation error, and one edge case.
- Run tests with `pytest -q` from the repo root.

## What not to do

- Don't add a database, ORM, or migrations. This is an in-memory demo.
- Don't add authentication.
- Don't add logging middleware.
- Don't restructure the project layout.
