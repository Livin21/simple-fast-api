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

from builder_agent import AgentConfig, BuilderAgent, RunResult, Sandbox


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

    success = result.status.value == "success" and verify.returncode == 0
    if not success:
        return 1

    # --- orchestrator: create PR from agent output ---
    _create_pr(
        project_root=project_root,
        sandbox_root=sandbox_root,
        source_repo=source_repo,
        result=result,
        task=TASK,
    )
    return 0


def _git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )


def _create_pr(
    project_root: Path,
    sandbox_root: Path,
    source_repo: Path,
    result: RunResult,
    task: str,
) -> None:
    """Orchestrator step: copy agent output to a branch and open a PR.

    This is the thin layer the architecture places between Code Gen and
    Review (§3.3 → §3.5). In production, this logic lives in the
    orchestrator service. Here it's inlined for the demo.
    """
    branch = f"agent/{result.run_id}"
    print(f"\n[orchestrator] creating PR on branch {branch}")

    # 1. Create a branch off main.
    _git(project_root, "checkout", "-b", branch)

    # 2. Copy changed files from sandbox back to demo_repo/.
    #    rsync-style: mirror sandbox into source_repo, skip non-repo noise.
    for item in sandbox_root.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(sandbox_root)
        # Skip files the sandbox created that aren't part of the repo
        # (e.g. __pycache__, .pytest_cache).
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        dest = source_repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)

    # 3. Commit.
    _git(project_root, "add", "demo_repo/")
    _git(
        project_root, "commit", "-m",
        f"agent: {result.final_message.splitlines()[0]}\n\n"
        f"Run ID: {result.run_id}\n"
        f"Iterations: {result.iterations}\n"
        f"Tokens: {result.input_tokens:,} in / {result.output_tokens:,} out\n"
        f"Wall clock: {result.wall_clock_s:.1f}s",
    )

    # 4. Push and open PR.
    _git(project_root, "push", "-u", "origin", branch)

    pr = subprocess.run(
        [
            "gh", "pr", "create",
            "--title", f"[agent] {task.splitlines()[0]}",
            "--body", (
                f"## Agent run\n\n"
                f"- **Run ID:** `{result.run_id}`\n"
                f"- **Status:** {result.status.value}\n"
                f"- **Iterations:** {result.iterations}\n"
                f"- **Tokens:** {result.input_tokens:,} in / {result.output_tokens:,} out\n"
                f"- **Wall clock:** {result.wall_clock_s:.1f}s\n"
                f"- **Audit log:** `{result.audit_path.name}`\n\n"
                f"## Task\n\n{task}\n\n"
                f"## Agent summary\n\n{result.final_message}"
            ),
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    # Switch back to main regardless of PR outcome.
    _git(project_root, "checkout", "main")

    if pr.returncode == 0:
        print(f"[orchestrator] PR created: {pr.stdout.strip()}")
    else:
        print(f"[orchestrator] PR creation failed: {pr.stderr.strip()}")


if __name__ == "__main__":
    sys.exit(main())
