"""Sandbox — bounds what the agent can touch.

For the demo, the sandbox is a filesystem chroot-lite: all paths are resolved
and checked to live under `root`, and bash runs with a wall-clock timeout.
This is NOT a security boundary against truly malicious code — it stops
mistakes, not attackers.

In production (architecture §4.5), the same interface is backed by a Docker
container for dev and Kata/Firecracker microVMs for multi-tenant prod. The
agent loop code doesn't change — only the Sandbox implementation does.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class SandboxError(Exception):
    """Raised when an operation is outside the sandbox or violates limits."""


@dataclass
class Sandbox:
    root: Path
    max_output_bytes: int
    max_bash_timeout_seconds: int

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.root.is_dir():
            raise SandboxError(f"sandbox root does not exist: {self.root}")

    # ----- path safety -----

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a sandbox-relative path and guarantee it's inside root.

        Rejects absolute paths, `..` escapes, and symlinks that point outside.
        """
        p = (self.root / rel_path).resolve()
        try:
            p.relative_to(self.root)
        except ValueError:
            raise SandboxError(f"path escapes sandbox: {rel_path}")
        return p

    # ----- file ops -----

    def read_file(self, rel_path: str) -> str:
        p = self._resolve(rel_path)
        if not p.is_file():
            raise SandboxError(f"not a file: {rel_path}")
        data = p.read_bytes()
        if len(data) > self.max_output_bytes:
            # Truncate with a clear marker — the agent needs to know it's
            # looking at a partial file.
            head = data[: self.max_output_bytes].decode("utf-8", errors="replace")
            return (
                head
                + f"\n\n[... truncated: file is {len(data)} bytes, "
                f"showing first {self.max_output_bytes} ...]"
            )
        return data.decode("utf-8", errors="replace")

    def write_file(self, rel_path: str, content: str) -> int:
        p = self._resolve(rel_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        p.write_bytes(data)
        return len(data)

    def list_dir(self, rel_path: str = ".") -> list[str]:
        p = self._resolve(rel_path)
        if not p.is_dir():
            raise SandboxError(f"not a directory: {rel_path}")
        entries = []
        for child in sorted(p.iterdir()):
            # Skip noise — matches what a dev would skip mentally.
            if child.name in {".git", "__pycache__", ".pytest_cache", "node_modules"}:
                continue
            suffix = "/" if child.is_dir() else ""
            entries.append(child.name + suffix)
        return entries

    # ----- bash -----

    def run_bash(self, command: str) -> dict[str, object]:
        """Run a bash command inside the sandbox root with a wall-clock limit.

        Returns a dict with stdout, stderr, returncode, and whether output was
        truncated. Never raises on non-zero exit — the agent should see the
        failure and react to it.
        """
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=self.max_bash_timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "returncode": -1,
                "stdout": (e.stdout or "")[: self.max_output_bytes],
                "stderr": (e.stderr or "") + f"\n[timeout after {self.max_bash_timeout_seconds}s]",
                "truncated": False,
                "timed_out": True,
            }

        stdout = result.stdout
        stderr = result.stderr
        truncated = False
        if len(stdout) > self.max_output_bytes:
            stdout = stdout[: self.max_output_bytes] + "\n[... stdout truncated ...]"
            truncated = True
        if len(stderr) > self.max_output_bytes:
            stderr = stderr[: self.max_output_bytes] + "\n[... stderr truncated ...]"
            truncated = True

        return {
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "timed_out": False,
        }
