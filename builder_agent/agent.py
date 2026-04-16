"""The Builder agent loop.

This is the core of the system. The shape is:

    initialize
    loop:
        check budget -> stop if exceeded
        call model with conversation so far
        record usage
        if stop_reason == 'end_turn':
            done -> return success
        if stop_reason == 'tool_use':
            execute each tool call via sandbox
            append tool_result messages
            continue

A few design choices worth calling out:

- Budget caps are checked BEFORE each model call, not just after. Otherwise
  a runaway iteration could overshoot by a full turn.
- Tool errors are returned to the model as tool_result with is_error=True.
  The model sees them and reacts. The loop itself does not raise.
- Every model turn and every tool call is logged to the audit file. If
  something goes wrong, the audit log is the single source of truth.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from .audit import AuditLog
from .config import AgentConfig
from .sandbox import Sandbox
from .tools import TOOLS, dispatch_tool


class RunStatus(str, Enum):
    SUCCESS = "success"
    BUDGET_EXCEEDED = "budget_exceeded"
    MAX_ITERATIONS = "max_iterations"
    MODEL_ERROR = "model_error"
    ESCALATE = "escalate_to_human"


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    iterations: int
    input_tokens: int
    output_tokens: int
    wall_clock_s: float
    final_message: str
    audit_path: Path


@dataclass
class BuilderAgent:
    """One agent run = one task against one sandbox.

    The agent is single-use; don't reuse across tasks. State is intentional —
    iteration count, token totals, conversation history — and keeping it local
    to the instance makes reasoning about budgets simpler.
    """

    config: AgentConfig
    sandbox: Sandbox
    client: Anthropic = field(default_factory=Anthropic)

    def run(self, task: str, audit_dir: Path) -> RunResult:
        run_id = f"run_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        audit = AuditLog(run_id=run_id, path=audit_dir / f"{run_id}.jsonl")

        system_prompt = self.config.system_prompt_path.read_text()
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": _format_task(task)}
        ]

        audit.record("task", {"task": task})
        audit.record("config", {
            "model": self.config.model,
            "caps": self.config.budget.__dict__,
        })

        start = time.time()
        iteration = 0
        total_input = 0
        total_output = 0
        total_tool_calls = 0
        final_message = ""
        status = RunStatus.MAX_ITERATIONS

        while True:
            # ----- budget checks (before each model call) -----
            elapsed = time.time() - start
            if iteration >= self.config.budget.max_iterations:
                status = RunStatus.MAX_ITERATIONS
                audit.record("budget_exceeded", {"reason": "max_iterations", "iteration": iteration})
                break
            if elapsed > self.config.budget.max_wall_clock_seconds:
                status = RunStatus.BUDGET_EXCEEDED
                audit.record("budget_exceeded", {"reason": "wall_clock", "elapsed_s": elapsed})
                break
            if total_input > self.config.budget.max_input_tokens:
                status = RunStatus.BUDGET_EXCEEDED
                audit.record("budget_exceeded", {"reason": "input_tokens", "total": total_input})
                break
            if total_tool_calls >= self.config.budget.max_tool_calls:
                status = RunStatus.BUDGET_EXCEEDED
                audit.record("budget_exceeded", {"reason": "tool_calls", "total": total_tool_calls})
                break

            iteration += 1
            audit.record("iteration_start", {"iteration": iteration, "elapsed_s": round(elapsed, 2)})

            # ----- model call -----
            try:
                response = self.client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.budget.max_output_tokens_per_call,
                    system=system_prompt,
                    tools=TOOLS,
                    messages=messages,
                )
            except Exception as e:
                audit.record("model_error", {"detail": repr(e)})
                status = RunStatus.MODEL_ERROR
                final_message = f"Model call failed: {e!r}"
                break

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            audit.record("model_response", {
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cumulative_input": total_input,
                "cumulative_output": total_output,
            })

            # Append the assistant message to history.
            messages.append({"role": "assistant", "content": response.content})

            # ----- terminal cases -----
            if response.stop_reason == "end_turn":
                # Model decided it's done.
                final_message = _extract_text(response.content)
                status = RunStatus.SUCCESS
                audit.record("end_turn", {"final_message_preview": final_message[:200]})
                break

            if response.stop_reason != "tool_use":
                # Unexpected stop reason (max_tokens, etc).
                final_message = f"Unexpected stop_reason: {response.stop_reason}"
                audit.record("unexpected_stop", {"stop_reason": response.stop_reason})
                status = RunStatus.ESCALATE
                break

            # ----- tool_use: execute each tool call, send results back -----
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                total_tool_calls += 1
                result_text, is_error = dispatch_tool(
                    name=block.name,
                    tool_input=block.input,
                    sandbox=self.sandbox,
                    audit=audit,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                    "is_error": is_error,
                })

            messages.append({"role": "user", "content": tool_results})

        # ----- finalize -----
        wall_clock = time.time() - start
        audit.record("run_finished", {
            "status": status.value,
            "iterations": iteration,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "tool_calls": total_tool_calls,
            "wall_clock_s": round(wall_clock, 2),
        })

        return RunResult(
            run_id=run_id,
            status=status,
            iterations=iteration,
            input_tokens=total_input,
            output_tokens=total_output,
            wall_clock_s=wall_clock,
            final_message=final_message or "(no final message)",
            audit_path=audit.path,
        )


def _format_task(task: str) -> str:
    """Wrap the task description with a standard framing."""
    return (
        "You have a fresh sandbox with a cloned repo. Start by reading "
        "CLAUDE.md and listing the repo root.\n\n"
        "## Environment\n\n"
        "- Your working directory is the repo root. All tool paths are "
        "relative to it — do not `cd` elsewhere.\n"
        "- Python: use `python -m pytest` to run tests.\n"
        "- Dependencies from requirements.txt are already installed.\n\n"
        f"## Task\n\n{task}\n\n"
        "Work step by step. Run tests after each meaningful change. "
        "When everything is green, emit your final summary message and stop."
    )


def _extract_text(content: list[Any]) -> str:
    """Get the plain-text portion of an assistant message."""
    parts = []
    for block in content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n\n".join(parts).strip()
