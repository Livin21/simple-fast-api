# Builder Agent

Reference implementation of the **Code Generation** stage from the broader AI-automated SDLC pipeline architecture. This is the only stage in that architecture that is *genuinely* agentic — it runs an iterative read → plan → edit → test → patch loop with tool use, budget caps, sandboxing, and an audit log.

It ships as a runnable demo: the agent extends a small FastAPI service with a new endpoint, and the driver independently verifies the result.

## What this demonstrates

- **A real agent loop** using Anthropic's tool use API directly, not wrapped in a framework. Every decision — budget checks, tool dispatch, termination — is visible in `agent.py`.
- **Four bounded tools** (`read_file`, `write_file`, `list_dir`, `run_bash`) scoped to a sandbox root. No framework magic.
- **Budget enforcement** on five dimensions (iterations, wall-clock, input tokens, tool calls, per-call output tokens), all checked before each model call.
- **Path-safe sandbox** that rejects `..` escapes, absolute paths, and symlink-out-of-root. The interface is abstracted so production can swap in Docker or Firecracker (architecture §4.5) without changing agent code.
- **Structured audit log** (JSONL) capturing every iteration, model response, tool call, tool result, and budget event. The audit log is the single source of truth for debugging a run.
- **Trust-but-verify driver**: after the agent claims success, the driver independently re-runs the test suite and compares.

## Quickstart

```bash
cd builder-agent
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python run_demo.py
```

Expected output: agent adds a `DELETE /items/{item_id}` endpoint + matching tests, all tests pass, audit log appears at `runs/run_<timestamp>_<id>.jsonl`.

## Project layout

```
builder-agent/
├── run_demo.py              # driver: fresh sandbox, runs agent, verifies
├── requirements.txt
├── builder_agent/
│   ├── agent.py             # the loop (~150 lines, single-use instance)
│   ├── config.py            # centralized tunables (caps, model, paths)
│   ├── sandbox.py           # path-safe filesystem + bash with timeout
│   ├── tools.py             # tool schemas + sandbox-routed dispatcher
│   ├── audit.py             # JSONL append-only event log
│   └── system_prompt.md     # behavior shaping
└── demo_repo/
    ├── CLAUDE.md            # repo conventions (the agent reads this first)
    ├── requirements.txt
    ├── app/main.py          # minimal FastAPI items service (baseline)
    └── tests/test_main.py   # 4 baseline tests (all passing before agent runs)
```

## The loop, in one paragraph

For each iteration: check every budget cap and stop if any tripped. Call the model with the full conversation history + tool schemas. Record token usage. If `stop_reason == "end_turn"`, the agent is done — return success with the final message. If `stop_reason == "tool_use"`, execute each tool call via the sandbox, format the results (including bash exit codes) as `tool_result` messages, append them to history, and loop. Tool errors come back as `tool_result` with `is_error=True` so the model can react. The loop itself never raises — any failure becomes a structured return value with a status code.

## Design decisions worth defending

**1. No agent framework.**
LangChain/LangGraph/CrewAI were considered. For this demo and for production, a ~150-line custom loop is clearer, debuggable, and easier to reason about than framework abstractions. When something goes wrong, you're reading your own code, not someone else's graph engine.

**2. Budget checks *before* each model call.**
Checking after would overshoot by a full turn. The five caps (iterations, wall-clock, input tokens, tool calls, per-call output tokens) are the escape hatches that prevent a runaway loop from burning through a budget.

**3. Tool errors are values, not exceptions.**
Sandbox violations, missing files, bash timeouts — all come back as `tool_result` with `is_error=True`. The model sees the failure and reacts. The loop does not raise on tool failures; it raises only on model API errors, and even then returns a structured `MODEL_ERROR` status rather than crashing.

**4. Single-use agent instances.**
Each run gets its own `BuilderAgent` instance. State (iteration count, token totals, conversation) is local. No shared mutable state across runs. Makes budget reasoning trivial.

**5. Sandbox as an interface.**
The filesystem-scoped sandbox here is not a security boundary against malicious code — it stops mistakes, not attackers. Production swaps it for Docker (internal) or Firecracker microVMs (multi-tenant) per architecture §4.5. The `Sandbox` class signature doesn't change.

**6. Audit log redacts `write_file` content.**
Full file contents live in the sandbox itself and in git after commit. The audit log stores SHA256 + byte count — enough to verify a write happened without making the log unreadable.

**7. Driver verifies independently.**
The agent claims "all tests passing." The driver then runs `pytest` itself and compares. An agent that lies or confuses itself is caught on the outside.

## What's deliberately left out

This is a reference implementation of one stage. Out of scope:

- **Git operations.** Commit/push/PR happen in a separate stage in the pipeline (Deploy, §3.6).
- **Dependency approval gate.** §2.1 of the architecture puts a human gate on new dependency additions. The agent here just obeys "don't add deps unless required" via the system prompt; production would intercept `pip install`/`npm install` and route it through an approval flow.
- **Prompt caching.** Would cut input token costs significantly in production. Omitted here for readability — add via `anthropic-beta: prompt-caching` headers when the system prompt + CLAUDE.md context exceed a threshold.
- **Multi-file context retrieval.** The agent reads files on demand here. The pipeline's shared Code Index (§4.3) would pre-fetch relevant files based on the change manifest; that integration lives at the orchestrator layer, not inside the agent.
- **Escalation to a stronger model.** When the agent tries twice and fails, the system prompt tells it to stop and report. Production would route that report to a human gate or retry with Opus.

## Inspecting an audit log

Each run writes a JSONL file under `runs/`. Useful queries:

```bash
# Show all tool calls in a run:
jq 'select(.event=="tool_call") | {name, input}' runs/run_*.jsonl

# Token burn by iteration:
jq 'select(.event=="model_response") | {i: .iteration, in: .input_tokens, out: .output_tokens}' runs/run_*.jsonl

# Final status:
jq 'select(.event=="run_finished")' runs/run_*.jsonl
```

## Mapping to the architecture doc

| Architecture section | Where it shows up here |
|---|---|
| §1.2 agentic vs. structured | This is the agentic stage — loop, tools, state |
| §3.3 Code Gen agent | The whole project |
| §3.3 sandbox + token cap + wall-clock | `sandbox.py`, `config.BudgetCaps` |
| §4.4 audit log | `audit.py` |
| §4.5 Docker → Firecracker escalation | Abstracted `Sandbox` interface |
| §5.1 offline eval sets | Out of scope here; the demo task itself is a 1-item eval |
| §2.1 new dep gate | Enforced via system prompt; production hook point noted above |
