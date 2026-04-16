"""Central config for the Builder agent.

All tuning knobs live here. Production would load these from env vars + a
per-tenant config in the orchestrator, but flat constants keep the demo readable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BudgetCaps:
    """Hard limits that stop a runaway agent.

    Every cap is enforced in the agent loop. When any one trips, the run ends
    with status=budget_exceeded and the best-effort output is returned for
    human review (per the architecture's escape-hatch design).
    """

    max_iterations: int = 15          # read/plan/edit/test cycles
    max_wall_clock_seconds: int = 900 # 15 min — matches architecture §3.3
    max_input_tokens: int = 500_000   # cumulative across all calls in a run
    max_output_tokens_per_call: int = 4096
    max_tool_calls: int = 50          # prevents read-the-whole-repo loops


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-sonnet-4-5"  # strong coding model; Haiku is too shallow
    system_prompt_path: Path = Path(__file__).parent / "system_prompt.md"
    budget: BudgetCaps = BudgetCaps()

    # Max bytes returned from a single read_file / run_bash — avoid
    # flooding context with a 10MB log file.
    max_tool_output_bytes: int = 50_000
    max_bash_timeout_seconds: int = 60
