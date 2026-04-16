"""Tool definitions and dispatcher.

The agent has four tools. Deliberately minimal — every added tool is another
thing that can be misused, and these four are enough for the full read/edit/
test/patch loop. Production additions (git, search) would go here.
"""
from __future__ import annotations

from typing import Any

from .audit import AuditLog
from .sandbox import Sandbox, SandboxError


# Anthropic tool_use API schema. Descriptions are part of the prompt — the
# model reads them to decide when to call each tool, so they matter.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read a file from the repo. Use this to understand existing code "
            "before modifying it. Paths are relative to the repo root. "
            "Large files are truncated with a marker — re-read with a "
            "different approach if the relevant part is cut off."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root, e.g. 'app/main.py'",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write the full contents of a file, overwriting if it exists. "
            "Use this for edits — read the file first, then write the "
            "complete updated contents. Do not write partial files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_dir",
        "description": (
            "List entries in a directory relative to the repo root. "
            "Directories are suffixed with '/'. Common noise dirs "
            "(.git, __pycache__, node_modules) are filtered out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to repo root. Default '.' for repo root.",
                    "default": ".",
                }
            },
            "required": [],
        },
    },
    {
        "name": "run_bash",
        "description": (
            "Run a bash command inside the repo. Use this to run tests "
            "(e.g. `pytest -q`), check syntax, or inspect files. "
            "There is a per-command timeout. Non-zero exit codes are "
            "returned, not raised — read stdout/stderr to understand failures."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
]


def dispatch_tool(
    name: str,
    tool_input: dict[str, Any],
    sandbox: Sandbox,
    audit: AuditLog,
) -> tuple[str, bool]:
    """Execute a tool call. Returns (result_string, is_error).

    Errors are returned as strings and flagged — they go back to the model as
    tool_result with is_error=True so it can react and retry. The agent loop
    does not raise on tool errors.
    """
    audit.record("tool_call", {"name": name, "input": _redact(tool_input)})

    try:
        if name == "read_file":
            out = sandbox.read_file(tool_input["path"])
            audit.record("tool_result", {"name": name, "bytes": len(out)})
            return out, False

        if name == "write_file":
            n = sandbox.write_file(tool_input["path"], tool_input["content"])
            audit.record("tool_result", {"name": name, "bytes_written": n, "path": tool_input["path"]})
            return f"Wrote {n} bytes to {tool_input['path']}", False

        if name == "list_dir":
            entries = sandbox.list_dir(tool_input.get("path", "."))
            audit.record("tool_result", {"name": name, "count": len(entries)})
            return "\n".join(entries) if entries else "(empty)", False

        if name == "run_bash":
            result = sandbox.run_bash(tool_input["command"])
            audit.record("tool_result", {
                "name": name,
                "returncode": result["returncode"],
                "timed_out": result["timed_out"],
            })
            # Format for the model. Include the exit code explicitly so
            # the model can't miss it.
            return _format_bash_result(tool_input["command"], result), False

        audit.record("tool_error", {"name": name, "reason": "unknown_tool"})
        return f"Unknown tool: {name}", True

    except SandboxError as e:
        audit.record("tool_error", {"name": name, "reason": "sandbox", "detail": str(e)})
        return f"Sandbox error: {e}", True
    except Exception as e:
        # Catch-all — the agent should see the error message, not crash.
        audit.record("tool_error", {"name": name, "reason": "exception", "detail": repr(e)})
        return f"Tool raised: {e!r}", True


def _format_bash_result(command: str, result: dict[str, Any]) -> str:
    parts = [f"$ {command}", f"exit code: {result['returncode']}"]
    if result["stdout"]:
        parts.append(f"--- stdout ---\n{result['stdout'].rstrip()}")
    if result["stderr"]:
        parts.append(f"--- stderr ---\n{result['stderr'].rstrip()}")
    if result["timed_out"]:
        parts.append("[command timed out]")
    return "\n".join(parts)


def _redact(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Keep full content out of the audit log for write_file — just hash+length.

    The model response and the final file state are logged elsewhere; this
    keeps the audit file readable.
    """
    if "content" in tool_input:
        import hashlib
        content = tool_input["content"]
        return {
            **{k: v for k, v in tool_input.items() if k != "content"},
            "content_sha256": hashlib.sha256(content.encode()).hexdigest()[:16],
            "content_bytes": len(content),
        }
    return tool_input
