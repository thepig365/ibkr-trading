"""CommandQueue abstraction for the local Strategy Lab UI.

Two concrete implementations:

* :class:`LocalCommandRunner` — subprocess runner that only invokes
  ``python -m bot.cli <subcommand>`` for subcommands present in
  :data:`bot_ui.services.safety.ALLOWED_COMMANDS`. All execution is
  bounded by :attr:`timeout_seconds` and stdout/stderr are captured so
  the UI can display them without `subprocess.PIPE` deadlock surprises.
* :class:`RemoteCommandQueue` — placeholder for the future Vercel
  deployment. Submitting a command from a Vercel UI route will
  eventually mean ``INSERT INTO commands ...`` against the shared
  Postgres / Supabase database, where a Worker process picks it up.

Both classes live behind the same :class:`CommandQueue` Protocol so
the UI route handlers do not care which one is wired up.

Hard rules enforced here, even if a UI bug tries to bypass them:

* Command name MUST be in :data:`ALLOWED_COMMANDS` (positive list).
* Command name MUST NOT contain any forbidden token (negative list).
* Each argument MUST be a string and MUST NOT contain shell
  metacharacters or live-trading flags.
* The runner uses a list-form ``subprocess.run`` (no ``shell=True``).
* Timeout is enforced; runaway processes are killed.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Protocol

from .safety import (
    ALLOWED_COMMANDS,
    FORBIDDEN_ARG_TOKENS,
    FORBIDDEN_COMMAND_TOKENS,
    is_allowed,
    is_forbidden,
)

DEFAULT_TIMEOUT_SECONDS = 600  # 10 minutes max per UI-issued command
DEFAULT_AUDIT_FILE = "data/auto_paper_loop/ui_commands.jsonl"


@dataclass(frozen=True)
class CommandRequest:
    """A single UI-originated command request, ready to be validated."""

    command: str
    args: tuple[str, ...] = ()
    requested_by: str = "local-ui"

    def display(self) -> str:
        if not self.args:
            return self.command
        return f"{self.command} {' '.join(shlex.quote(a) for a in self.args)}"


@dataclass
class CommandResult:
    """Outcome of running (or refusing) a CommandRequest."""

    request: CommandRequest
    accepted: bool
    rejected_reason: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    started_utc: str = ""
    finished_utc: str = ""
    duration_seconds: float | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def ok(self) -> bool:
        return self.accepted and self.exit_code == 0

    @property
    def status_label(self) -> str:
        if not self.accepted:
            return "REJECTED"
        if self.exit_code is None:
            return "RUNNING"
        return "OK" if self.exit_code == 0 else f"FAIL ({self.exit_code})"

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["request"] = {
            "command": self.request.command,
            "args": list(self.request.args),
            "requested_by": self.request.requested_by,
        }
        d["status_label"] = self.status_label
        return d


class CommandQueue(Protocol):
    """Submit a command for execution. Returns the result synchronously today."""

    def submit(self, request: CommandRequest) -> CommandResult: ...
    def list_recent(self, limit: int = 20) -> list[CommandResult]: ...


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_request(request: CommandRequest) -> tuple[bool, str]:
    """Return (accepted, reason). Reason is empty when accepted."""
    cmd = (request.command or "").strip()
    if not cmd:
        return False, "Empty command name."
    if is_forbidden(cmd):
        return False, f"Command name {cmd!r} contains a forbidden token."
    if not is_allowed(cmd):
        return False, f"Command {cmd!r} is not on the UI allowlist."
    for arg in request.args:
        if not isinstance(arg, str):
            return False, "All command arguments must be strings."
        if "\n" in arg or "\r" in arg:
            return False, "Newlines are not allowed in command arguments."
        lowered = arg.strip().lower()
        for tok in FORBIDDEN_ARG_TOKENS:
            if tok in lowered:
                return False, f"Argument contains forbidden token {tok!r}."
    return True, ""


# ---------------------------------------------------------------------------
# Local backend
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LocalCommandRunner:
    """Runs allowlisted ``python -m bot.cli`` subcommands as subprocesses.

    * Never uses ``shell=True``.
    * Never passes ``--live`` / ``--enable-live-trading`` (validated).
    * Captures stdout/stderr.
    * Hard-kills the process if it exceeds :attr:`timeout_seconds`.
    * Appends an audit row to ``data/auto_paper_loop/ui_commands.jsonl``.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        python_executable: str | None = None,
        audit_file: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.timeout_seconds = int(timeout_seconds)
        self.python_executable = python_executable or sys.executable
        self.audit_file = (
            Path(audit_file)
            if audit_file is not None
            else self.project_root / DEFAULT_AUDIT_FILE
        )
        self._recent: list[CommandResult] = []

    # ------------------------------------------------------------------
    def submit(self, request: CommandRequest) -> CommandResult:
        accepted, reason = validate_request(request)
        result = CommandResult(
            request=request,
            accepted=accepted,
            rejected_reason="" if accepted else reason,
            started_utc=_utc_now_iso(),
        )
        if not accepted:
            result.finished_utc = result.started_utc
            result.duration_seconds = 0.0
            self._record(result)
            return result

        argv: list[str] = [
            self.python_executable,
            "-m",
            "bot.cli",
            request.command,
            *request.args,
        ]
        env = os.environ.copy()
        # The UI must never let a child process turn live trading on, even
        # if a future config change loosens defaults. Force paper mode
        # for the subprocess environment.
        env["IBKR_ACCOUNT_MODE"] = "paper"

        t0 = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv is fully validated above
                argv,
                cwd=str(self.project_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            result.exit_code = int(completed.returncode)
            result.stdout = (completed.stdout or "")[-200_000:]
            result.stderr = (completed.stderr or "")[-200_000:]
        except subprocess.TimeoutExpired as exc:
            result.exit_code = 124
            result.stdout = ((exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "")).__str__()[
                -200_000:
            ]
            result.stderr = (
                f"TIMEOUT after {self.timeout_seconds}s\n"
                + (
                    (exc.stderr or b"") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                ).__str__()[-200_000:]
            )
        except FileNotFoundError as exc:
            result.exit_code = 127
            result.stderr = f"Python executable or bot.cli not found: {exc}"
        except OSError as exc:
            result.exit_code = 126
            result.stderr = f"OS error launching subprocess: {exc}"
        finally:
            t1 = time.monotonic()
            result.finished_utc = _utc_now_iso()
            result.duration_seconds = round(t1 - t0, 3)
            self._record(result)
        return result

    # ------------------------------------------------------------------
    def list_recent(self, limit: int = 20) -> list[CommandResult]:
        return list(self._recent[-int(limit) :][::-1])

    # ------------------------------------------------------------------
    def _record(self, result: CommandResult) -> None:
        self._recent.append(result)
        # Keep in-memory ring small so a long-running UI does not bloat.
        if len(self._recent) > 100:
            self._recent = self._recent[-100:]
        try:
            self.audit_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "id": result.id,
                "ts": result.started_utc,
                "command": result.request.command,
                "args": list(result.request.args),
                "requested_by": result.request.requested_by,
                "accepted": result.accepted,
                "rejected_reason": result.rejected_reason,
                "exit_code": result.exit_code,
                "duration_seconds": result.duration_seconds,
            }
            with self.audit_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            # Audit best-effort; never break UI on disk errors.
            pass


# ---------------------------------------------------------------------------
# Remote backend (placeholder)
# ---------------------------------------------------------------------------


class RemoteCommandQueue:
    """Future Vercel UI -> Postgres ``commands`` table -> Worker.

    Not implemented. Imports cleanly so the architectural seam is
    visible. Activate by setting ``STRATEGY_LAB_BACKEND=remote`` once
    the cloud deployment exists.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ.get("DATABASE_URL", "")

    def _not_yet(self) -> NotImplementedError:
        return NotImplementedError(
            "RemoteCommandQueue is a placeholder. Set STRATEGY_LAB_BACKEND=local "
            "for local subprocess execution, or implement this once the worker "
            "and DATABASE_URL exist."
        )

    def submit(self, request: CommandRequest) -> CommandResult:  # pragma: no cover - stub
        raise self._not_yet()

    def list_recent(self, limit: int = 20) -> list[CommandResult]:  # pragma: no cover - stub
        raise self._not_yet()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_command_queue(project_root: Path) -> CommandQueue:
    backend = (os.environ.get("STRATEGY_LAB_BACKEND") or "local").strip().lower()
    if backend == "local":
        return LocalCommandRunner(project_root)
    if backend == "remote":
        return RemoteCommandQueue()
    raise ValueError(
        f"Unknown STRATEGY_LAB_BACKEND={backend!r}. Use 'local' or 'remote'."
    )


__all__ = [
    "CommandRequest",
    "CommandResult",
    "CommandQueue",
    "LocalCommandRunner",
    "RemoteCommandQueue",
    "validate_request",
    "get_command_queue",
    "ALLOWED_COMMANDS",
    "FORBIDDEN_ARG_TOKENS",
    "FORBIDDEN_COMMAND_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
]
