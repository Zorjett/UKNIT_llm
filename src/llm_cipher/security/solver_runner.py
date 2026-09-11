"""Strict, auditable subprocess wrapper for SAT solvers such as Kissat.

The wrapper deliberately exposes four outcomes: ``sat``, ``unsat``,
``timeout``, and ``error``.  In particular, a timeout or malformed solver
output is never interpreted as UNSAT.
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence


SolverStatus = Literal["sat", "unsat", "timeout", "error"]
SAT_STATUS_LINE = "s SATISFIABLE"
UNSAT_STATUS_LINE = "s UNSATISFIABLE"


@dataclass(frozen=True)
class SolverResult:
    """Complete, immutable record of one solver invocation."""

    status: SolverStatus
    command: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int | None
    elapsed_s: float
    assignment: tuple[int, ...] = ()
    error: str | None = None

    def assignment_map(self) -> dict[int, bool]:
        """Return the parsed signed literals as ``variable -> truth value``."""

        return {abs(literal): literal > 0 for literal in self.assignment}


def run_solver(
    command: Sequence[str | os.PathLike[str]],
    timeout_s: float,
    *,
    require_witness: bool = False,
    expected_variables: int | None = None,
    termination_grace_s: float = 0.25,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> SolverResult:
    """Run a SAT solver and strictly classify its result.

    ``command`` must be a sequence of separate arguments; no shell command
    string is accepted and ``shell=True`` is never used.  When
    ``expected_variables`` is supplied, a SAT witness must assign every
    variable from 1 through that number.
    """

    checked_command = _validate_command(command)
    _validate_options(timeout_s, expected_variables, termination_grace_s)
    started_at = time.monotonic()

    popen_options: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "shell": False,
    }
    if cwd is not None:
        popen_options["cwd"] = os.fspath(cwd)
    if env is not None:
        popen_options["env"] = dict(env)

    # A new POSIX session gives the solver its own process group, allowing a
    # timeout to terminate descendants as well as the immediate child.
    if os.name == "posix":
        popen_options["start_new_session"] = True
    elif os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        process = subprocess.Popen(checked_command, **popen_options)
    except (OSError, ValueError) as exc:
        return SolverResult(
            status="error",
            command=checked_command,
            stdout="",
            stderr="",
            returncode=None,
            elapsed_s=time.monotonic() - started_at,
            error=f"failed to start solver: {type(exc).__name__}: {exc}",
        )

    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        stdout, stderr = _stop_after_timeout(process, termination_grace_s)
        return SolverResult(
            status="timeout",
            command=checked_command,
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            elapsed_s=time.monotonic() - started_at,
            error=f"solver exceeded timeout of {timeout_s:g} seconds",
        )

    elapsed_s = time.monotonic() - started_at
    return _classify_completed_process(
        checked_command,
        stdout,
        stderr,
        process.returncode,
        elapsed_s,
        require_witness=require_witness,
        expected_variables=expected_variables,
    )


def _validate_command(
    command: Sequence[str | os.PathLike[str]],
) -> tuple[str, ...]:
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence):
        raise TypeError("command must be a sequence of separate arguments")
    if not command:
        raise ValueError("command must not be empty")

    checked: list[str] = []
    for argument in command:
        try:
            value = os.fspath(argument)
        except TypeError as exc:
            raise TypeError("every command argument must be a string or path") from exc
        if not isinstance(value, str):
            raise TypeError("every command argument must resolve to text")
        if "\x00" in value:
            raise ValueError("command arguments must not contain NUL characters")
        checked.append(value)
    if not checked[0]:
        raise ValueError("solver executable must not be empty")
    return tuple(checked)


def _validate_options(
    timeout_s: float,
    expected_variables: int | None,
    termination_grace_s: float,
) -> None:
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("timeout_s must be a finite positive number")
    if (
        expected_variables is not None
        and (
            isinstance(expected_variables, bool)
            or not isinstance(expected_variables, int)
            or expected_variables < 1
        )
    ):
        raise ValueError("expected_variables must be a positive integer")
    if (
        isinstance(termination_grace_s, bool)
        or not isinstance(termination_grace_s, (int, float))
        or not math.isfinite(termination_grace_s)
        or termination_grace_s < 0
    ):
        raise ValueError("termination_grace_s must be a finite non-negative number")


def _stop_after_timeout(
    process: subprocess.Popen[str], grace_s: float
) -> tuple[str, str]:
    _signal_process_tree(process, signal.SIGTERM)
    try:
        return process.communicate(timeout=grace_s)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGKILL)
        return process.communicate()


def _signal_process_tree(
    process: subprocess.Popen[str], requested_signal: signal.Signals
) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, requested_signal)
        elif requested_signal == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _classify_completed_process(
    command: tuple[str, ...],
    stdout: str,
    stderr: str,
    returncode: int,
    elapsed_s: float,
    *,
    require_witness: bool,
    expected_variables: int | None,
) -> SolverResult:
    stripped_lines = [line.strip() for line in stdout.splitlines()]
    has_sat = SAT_STATUS_LINE in stripped_lines
    has_unsat = UNSAT_STATUS_LINE in stripped_lines

    error: str | None = None
    assignment: tuple[int, ...] = ()

    if has_sat and has_unsat:
        error = "solver output contains both SAT and UNSAT status lines"
    elif not has_sat and not has_unsat:
        error = "solver output contains no exact SAT/UNSAT status line"
    elif has_sat and returncode != 10:
        error = f"SAT status line conflicts with exit code {returncode}"
    elif has_unsat and returncode != 20:
        error = f"UNSAT status line conflicts with exit code {returncode}"

    if error is None and has_sat:
        try:
            assignment = _parse_assignment(stripped_lines)
        except ValueError as exc:
            error = str(exc)
        else:
            if require_witness and not assignment:
                error = "SAT result requires a witness but no v assignment was found"
            elif expected_variables is not None:
                actual = {abs(literal) for literal in assignment}
                missing = [
                    variable
                    for variable in range(1, expected_variables + 1)
                    if variable not in actual
                ]
                if missing:
                    preview = ", ".join(str(value) for value in missing[:8])
                    suffix = "..." if len(missing) > 8 else ""
                    error = (
                        "SAT witness is incomplete; missing variables "
                        f"{preview}{suffix}"
                    )
    elif error is None and has_unsat:
        if any(line == "v" or line.startswith("v ") for line in stripped_lines):
            error = "UNSAT result unexpectedly contains assignment lines"

    if error is not None:
        return SolverResult(
            status="error",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            elapsed_s=elapsed_s,
            assignment=assignment,
            error=error,
        )

    return SolverResult(
        status="sat" if has_sat else "unsat",
        command=command,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
        elapsed_s=elapsed_s,
        assignment=assignment,
    )


def _parse_assignment(lines: Sequence[str]) -> tuple[int, ...]:
    assignments: dict[int, bool] = {}
    saw_assignment_line = False
    terminated = False

    for line in lines:
        if not (line == "v" or line.startswith("v ")):
            continue
        saw_assignment_line = True
        for token in line.split()[1:]:
            try:
                literal = int(token)
            except ValueError as exc:
                raise ValueError(f"invalid token in SAT assignment: {token!r}") from exc
            if terminated:
                if literal != 0:
                    raise ValueError("SAT assignment contains data after terminating zero")
                continue
            if literal == 0:
                terminated = True
                continue
            variable = abs(literal)
            previous = assignments.get(variable)
            value = literal > 0
            if previous is not None and previous != value:
                raise ValueError(
                    f"SAT assignment gives conflicting values for variable {variable}"
                )
            assignments[variable] = value

    if saw_assignment_line and not terminated:
        raise ValueError("SAT assignment is not terminated by zero")
    return tuple(
        variable if value else -variable
        for variable, value in sorted(assignments.items())
    )
