"""Run the Builder agent against the demo FastAPI repo.

Usage:
    ANTHROPIC_API_KEY=... python run_demo.py

The demo repo is copied to a scratch sandbox first so repeated runs start
from a clean slate. The agent's task is to add a DELETE endpoint — simple
enough to verify by eye, realistic enough to exercise the full loop
(read existing code, understand conventions, write code + tests, run pytest).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from builder_agent import AgentConfig, BuilderAgent, Sandbox


TASK = """
Add a DELETE endpoint to the items service.

Requirements:
- Route: DELETE /items/{item_id}
- Success: returns 204 No Content and removes the item from the store
- Missing item: returns 404 with a clear error detail
- Add tests covering both cases in tests/test_main.py

Follow the conventions in CLAUDE.md. Do not modify existing tests.
""".strip()


def main() -> int:
    project_root = Path(__file__).parent
    source_repo = project_root / "demo_repo"
    sandbox_root = project_root / "sandbox_scratch"
    audit_dir = project_root / "runs"

    # Fresh sandbox every run — otherwise the agent's previous edits leak in.
    if sandbox_root.exists():
        shutil.rmtree(sandbox_root)
    shutil.copytree(source_repo, sandbox_root)
    print(f"[setup] sandbox prepared at {sandbox_root}")

    # Pre-install deps so the agent doesn't waste iterations on env setup.
    req_file = sandbox_root / "requirements.txt"
    if req_file.exists():
        print("[setup] installing dependencies...")
        subprocess.run(
            ["uv", "pip", "install", "-q", "-r", str(req_file)],
            cwd=sandbox_root,
            check=True,
            capture_output=True,
        )
        print("[setup] dependencies installed")

    config = AgentConfig()
    sandbox = Sandbox(
        root=sandbox_root,
        max_output_bytes=config.max_tool_output_bytes,
        max_bash_timeout_seconds=config.max_bash_timeout_seconds,
    )

    agent = BuilderAgent(config=config, sandbox=sandbox)

    print(f"[run] starting agent with task:\n{TASK}\n")
    result = agent.run(task=TASK, audit_dir=audit_dir)

    print("\n" + "=" * 60)
    print(f"status:        {result.status.value}")
    print(f"iterations:    {result.iterations}")
    print(f"input tokens:  {result.input_tokens:,}")
    print(f"output tokens: {result.output_tokens:,}")
    print(f"wall clock:    {result.wall_clock_s:.1f}s")
    print(f"audit log:     {result.audit_path}")
    print("=" * 60)
    print("\n--- final message ---")
    print(result.final_message)
    print()

    # Post-run verification: agent claims green tests; confirm from the outside.
    verify = subprocess.run(
        ["python", "-m", "pytest", "-q"],
        cwd=sandbox_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    print("--- independent test verification ---")
    print(verify.stdout)
    if verify.returncode != 0:
        print(f"[!] tests failed (exit {verify.returncode})")
        print(verify.stderr)

    return 0 if result.status.value == "success" and verify.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
