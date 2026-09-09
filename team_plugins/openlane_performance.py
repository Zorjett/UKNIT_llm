"""OpenLane-backed latency measurement for Team C.

The evaluator intentionally has no placeholder path.  A candidate is first
materialized as a synthesizable Verilog design, then OpenLane is invoked in a
separate work directory.  The result is accepted only when a finite critical
path delay can be extracted from OpenLane's JSON/text reports.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

from analysis import latency_computation as latency
import config as _config


class OpenLaneLatencyError(RuntimeError):
    """OpenLane did not produce a usable latency measurement."""


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if math.isfinite(value) and value > 0 else default


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    value = str(candidate.get("candidate_id") or "candidate")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:80] or "candidate"


def _wsl_path(path: Path) -> str:
    """Convert a Windows drive path to the usual /mnt/<drive> WSL path."""
    value = str(path).replace("\\", "/")
    if len(value) >= 2 and value[1] == ":":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


def _wsl_command(distro: str, executable: str, config_path: Path) -> list[str]:
    """Run OpenLane from its WSL work directory so Docker can mount it."""
    return [
        "wsl",
        "-d",
        distro,
        "bash",
        "-lc",
        "cd " + shlex.quote(_wsl_path(config_path.parent))
        + " && exec " + shlex.quote(executable)
        + " --dockerized --to OpenROAD.STAPostPNR config.json",
    ]


def _matrix_rows(matrix: Any) -> list[list[int]]:
    if not isinstance(matrix, list) or len(matrix) != 64:
        raise OpenLaneLatencyError("linear_matrix must be a 64x64 list")
    rows: list[list[int]] = []
    for row in matrix:
        if not isinstance(row, list) or len(row) != 64:
            raise OpenLaneLatencyError("linear_matrix must be a 64x64 list")
        rows.append([int(value) for value in row])
    return rows


def _render_verilog(candidate: Mapping[str, Any], top: str, output: Path) -> None:
    rounds = candidate.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise OpenLaneLatencyError("candidate has no rounds")
    num_rounds = len(rounds)
    statements: list[str] = []
    statements.extend(latency.prepare_preamble(num_rounds, top + ".v"))
    for n in range(num_rounds + 1):
        statements.append(latency.get_key_schedule_and_const(n % 2, n, n))
    statements.append("\tassign t[0] = x ^ kn[0];")
    for n in range(1, num_rounds):
        statements.append(latency.get_add_key(3 * n - 1, n))
    last_key_index = num_rounds - 1
    statements.append("\tassign t[%i] = t[%i] ^ kn[%i];" % (3 * last_key_index + 2, 3 * last_key_index + 1, num_rounds))
    statements.append("\tassign y = t[%i];" % (3 * last_key_index + 2))
    for n in range(num_rounds):
        statements.append(latency.get_subst_layer(3 * n, 2 * n))
    for n in range(1, num_rounds):
        statements.append(latency.get_linear_layer(3 * n - 2, 2 * n - 1))
    statements.append("\tendmodule\n")

    for n, round_value in enumerate(rounds):
        sboxes = round_value.get("sboxes") if isinstance(round_value, Mapping) else None
        if not isinstance(sboxes, list) or len(sboxes) != 16:
            raise OpenLaneLatencyError(f"round {n} must contain 16 S-boxes")
        statements.append(latency.get_sboxes_in_subst_layer(2 * n))
        for index, sbox in enumerate(sboxes):
            if not isinstance(sbox, list) or len(sbox) != 16:
                raise OpenLaneLatencyError(f"round {n} S-box {index} is malformed")
            statements.append(latency.get_sboxes_implementation(sbox, 2 * n, index))
        if n < num_rounds - 1:
            matrix = _matrix_rows(round_value.get("linear_matrix"))
            statements.append(latency.get_matrix_implementation(matrix, 2 * n + 1))
    statements.append(latency.get_key_add_and_const_implementation())
    output.write_text("\n".join(statements) + "\n", encoding="utf-8")


def _json_numbers(value: Any, key_hint: str = "") -> Iterable[tuple[str, float]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _json_numbers(item, str(key))
    elif isinstance(value, list):
        for item in value:
            yield from _json_numbers(item, key_hint)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number):
            yield key_hint.lower(), number


def _extract_latency(workdir: Path, clock_period_ns: float) -> tuple[float, str, str]:
    """Extract critical-path delay in ns from OpenLane reports.

    OpenLane 2 metrics commonly expose setup worst slack.  In that case delay
    is derived as ``clock_period - worst_slack``.  Direct delay/critical-path
    metrics and textual ``data arrival time`` reports are also accepted.
    """
    direct_keys = {"critical_path_delay", "max_delay", "path_delay", "delay_ns", "delay"}
    slack_values: list[float] = []
    direct_values: list[float] = []
    for path in workdir.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        for key, number in _json_numbers(payload):
            if key in direct_keys or key.endswith("critical_path_delay") or key.endswith("max_delay"):
                direct_values.append(number)
            if "worst_slack" in key or key.endswith("setup__ws") or key.endswith("setup_ws"):
                slack_values.append(number)
    if direct_values:
        value = max(direct_values)
        if value > 0:
            return value, "ns", "json_direct_delay"
    if slack_values:
        value = clock_period_ns - min(slack_values)
        if math.isfinite(value) and value > 0:
            return value, "ns", "json_setup_worst_slack"

    # OpenROAD reports the value immediately *before* the label, e.g.
    # ``4.182183   data arrival time``.  Some tool versions print the label
    # first, so accept both layouts and ignore the signed required-time copy.
    patterns = (
        re.compile(r"([+-]?[0-9]+(?:\.[0-9]+)?)\s+data\s+arrival\s+time", re.I),
        re.compile(r"data\s+arrival\s+time\s+([+-]?[0-9]+(?:\.[0-9]+)?)", re.I),
    )
    for path in workdir.rglob("*.rpt"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matches = []
        for pattern in patterns:
            matches.extend(float(item) for item in pattern.findall(text))
        if matches and max(matches) > 0:
            return max(matches), "ns", "text_data_arrival_time"
    raise OpenLaneLatencyError("OpenLane completed but no critical-path latency was found in its reports")


def _command(workdir: Path, config_path: Path) -> list[str]:
    def with_latency_stage(command: list[str]) -> list[str]:
        """Stop the sequential flow once post-PNR STA has produced timing."""
        if "--to" in command or "OpenROAD.STAPostPNR" in command:
            return command
        try:
            config_index = command.index(str(config_path))
        except ValueError:
            config_index = len(command)
        return command[:config_index] + ["--to", "OpenROAD.STAPostPNR"] + command[config_index:]

    configured = (
        os.getenv("UKNIT_OPENLANE_COMMAND", "").strip()
        or str(getattr(_config, "OPENLANE", {}).get("COMMAND", "") or "").strip()
    )
    if configured:
        command = shlex.split(
            configured.format(config=str(config_path), workdir=str(workdir)),
            posix=False,
        )
        # A Windows environment often contains the documented Linux command
        # ``openlane --dockerized ...``.  If that executable is not on the
        # Windows PATH, transparently route it through the configured WSL
        # distribution instead of returning shell exit code 127.
        if command and command[0].lower() == "openlane" and not shutil.which("openlane"):
            distro = os.getenv("UKNIT_OPENLANE_WSL_DISTRO", "Ubuntu-24.04").strip()
            executable = os.getenv(
                "UKNIT_OPENLANE_WSL_EXECUTABLE",
                "/home/mnzn/venvs/openlane/bin/openlane",
            ).strip()
            dockerized = "--dockerized" in command[1:]
            return _wsl_command(distro, executable, config_path)
        return with_latency_stage(command)
    if shutil.which("openlane"):
        return ["openlane", "--dockerized", "--to", "OpenROAD.STAPostPNR", str(config_path)]
    # When the project is launched with an interpreter from the OpenLane
    # virtual environment, its bin directory is not always exported in PATH
    # (notably from WSL mounted Windows paths). Use the known executable
    # directly before falling back to a Windows -> WSL bridge.
    local_executable = os.getenv(
        "UKNIT_OPENLANE_WSL_EXECUTABLE",
        "/home/mnzn/venvs/openlane/bin/openlane",
    ).strip()
    if os.name != "nt" and Path(local_executable).is_file():
        return [
            local_executable,
            "--dockerized",
            "--to",
            "OpenROAD.STAPostPNR",
            str(config_path),
        ]
    if shutil.which("wsl"):
        # OpenLane 2 is normally installed in a WSL virtual environment.
        # Do not rely on the WSL login PATH: Windows-launched ``wsl`` often
        # lands in docker-desktop or a shell where the venv is not activated.
        distro = os.getenv("UKNIT_OPENLANE_WSL_DISTRO", "Ubuntu-24.04").strip()
        executable = local_executable
        return _wsl_command(distro, executable, config_path)
    raise OpenLaneLatencyError(
        "OpenLane executable not found; install OpenLane 2 or set UKNIT_OPENLANE_COMMAND"
    )


def evaluate_performance(candidate: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run OpenLane for one candidate and return measured latency in ns."""
    context = dict(context or {})
    configured_root = (
        os.getenv("UKNIT_OPENLANE_WORK_ROOT", "").strip()
        or str(getattr(_config, "OPENLANE", {}).get("WORK_ROOT", "") or "").strip()
    )
    root = Path(configured_root)
    if not root:
        root = Path(context.get("work_dir") or tempfile.gettempdir()) / "uknit_openlane_runs"
    root.mkdir(parents=True, exist_ok=True)
    candidate_name = _candidate_id(candidate)
    fingerprint = str(candidate.get("fingerprint") or "unknown")[:16]
    workdir = root / f"{candidate_name}_{fingerprint}"
    if workdir.exists():
        shutil.rmtree(workdir)
    srcdir = workdir / "src"
    srcdir.mkdir(parents=True)
    top = f"uknit_{candidate_name}_{fingerprint}".replace("-", "_")
    verilog_path = srcdir / f"{top}.v"
    _render_verilog(candidate, top, verilog_path)
    configured_clock = getattr(_config, "OPENLANE", {}).get("CLOCK_PERIOD_NS", 10.0)
    clock_period_ns = _env_float("UKNIT_OPENLANE_CLOCK_PERIOD_NS", float(configured_clock))
    config_path = workdir / "config.json"
    command = _command(workdir, config_path)
    wsl_execution = bool(command and command[0].lower() == "wsl")
    design_dir_value = _wsl_path(workdir) if wsl_execution else str(workdir)
    config_path.write_text(json.dumps({
        "DESIGN_NAME": top,
        "VERILOG_FILES": [f"dir::src/{top}.v"],
        "CLOCK_PORT": "clk",
        "CLOCK_PERIOD": clock_period_ns,
        # The generated cipher has 257 top-level IO bits (192 inputs,
        # 64 outputs, and clk).  OpenLane's default relative floorplan is too
        # small for that many pins, so use a conservative absolute die area.
        "FP_SIZING": "absolute",
        "DIE_AREA": [0, 0, 450, 450],
        "DESIGN_DIR": design_dir_value,
    }, indent=2), encoding="utf-8")
    timeout_seconds = _env_int(
        "UKNIT_OPENLANE_TIMEOUT_SECONDS",
        int(getattr(_config, "OPENLANE", {}).get("TIMEOUT_SECONDS", 3600)),
    )
    started = time.monotonic()
    print(
        "[openlane] start candidate=%s generation=%s population=%s; logs=%s"
        % (
            candidate_name,
            context.get("generation", "?"),
            context.get("population_index", "?"),
            workdir,
        ),
        flush=True,
    )
    heartbeat_stop = threading.Event()

    def _heartbeat() -> None:
        while not heartbeat_stop.wait(30.0):
            elapsed = int(time.monotonic() - started)
            print(
                "[openlane] still running candidate=%s elapsed=%ss"
                % (candidate_name, elapsed),
                flush=True,
            )

    heartbeat = threading.Thread(target=_heartbeat, name="openlane-progress", daemon=True)
    heartbeat.start()
    try:
        completed = subprocess.run(
            command,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1.0)
    (workdir / "openlane.stdout.log").write_text(completed.stdout or "", encoding="utf-8")
    (workdir / "openlane.stderr.log").write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise OpenLaneLatencyError(
            f"OpenLane failed with exit code {completed.returncode}; see {workdir}"
        )
    value, units, source = _extract_latency(workdir, clock_period_ns)
    if not math.isfinite(value) or value <= 0:
        raise OpenLaneLatencyError("OpenLane returned an invalid latency")
    print(
        "[openlane] complete candidate=%s elapsed=%ss latency=%s %s"
        % (candidate_name, int(time.monotonic() - started), value, units),
        flush=True,
    )
    return {
        "status": "ok",
        "valid": True,
        "metrics": {
            "latency": value,
            "area": None,
            "energy": None,
            "units": {"latency": units},
        },
        "warnings": [],
        "errors": [],
        "artifacts": {"workdir": str(workdir), "source": source},
    }


__all__ = ["OpenLaneLatencyError", "evaluate_performance"]
