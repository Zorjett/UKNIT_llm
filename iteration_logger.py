"""Structured JSONL logging for the uKNIT search loop.

The logger deliberately depends only on the public attributes of Generation and
Member.  Analysis, mutation, and verification plugins can therefore attach
their own JSON-compatible fields to ``changes`` without importing this module.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping


_MEMBER_FIELDS = (
    "candidate_id",
    "identifier",
    "pop_index",
    "gen_index",
    "num_rounds",
    "fitness",
    "diversity",
    "latency",
    "security_diff",
    "security_linear",
    "evaluation_status",
    "evaluation_error",
    "plugin_security",
    "plugin_validation",
    "plugin_performance",
    "is_elite",
    "parent_ids",
    "crossover_strategy",
    "crossover_details",
    "mutation_changes",
)


def json_safe(value: Any, _seen: set[int] | None = None) -> Any:
    """Return a recursively JSON-serializable representation of ``value``.

    NumPy values are supported through their standard ``item``/``tolist``
    methods without making NumPy a dependency of the logging layer.  Unknown
    objects are represented by their public attributes, or by ``repr`` when no
    useful public state is available.
    """

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return json_safe(value.value, _seen)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()

    if _seen is None:
        _seen = set()
    object_id = id(value)
    if object_id in _seen:
        return "<recursive:%s>" % type(value).__name__
    _seen.add(object_id)

    try:
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: json_safe(getattr(value, field.name), _seen)
                for field in fields(value)
            }

        if isinstance(value, Mapping):
            return {
                str(json_safe(key, _seen)): json_safe(item, _seen)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [json_safe(item, _seen) for item in value]
        if isinstance(value, (set, frozenset)):
            safe_items = [json_safe(item, _seen) for item in value]
            return sorted(safe_items, key=lambda item: repr(item))

        item_method = getattr(value, "item", None)
        if callable(item_method):
            try:
                item = item_method()
            except (TypeError, ValueError):
                pass
            else:
                if item is not value:
                    return json_safe(item, _seen)

        tolist_method = getattr(value, "tolist", None)
        if callable(tolist_method):
            try:
                listed = tolist_method()
            except (TypeError, ValueError):
                pass
            else:
                if listed is not value:
                    return json_safe(listed, _seen)

        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, Mapping):
            public_attributes = {
                key: item
                for key, item in attributes.items()
                if not str(key).startswith("_") and not callable(item)
            }
            if public_attributes:
                return json_safe(public_attributes, _seen)

        return repr(value)
    finally:
        _seen.discard(object_id)


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _component_payload(round_function: Any) -> dict[str, Any]:
    substitution = _get(round_function, "substitution")
    linear = _get(round_function, "linear")
    return {
        "round_index": json_safe(_get(round_function, "round_index")),
        "sboxes": json_safe(_get(substitution, "sboxes", substitution)),
        "linear_matrix": json_safe(_get(linear, "matrix", linear)),
    }


def _digest(value: Any) -> str:
    encoded = json.dumps(
        json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _round_summary(round_function: Any, include_components: bool) -> dict[str, Any]:
    payload = _component_payload(round_function)
    summary = {
        "round_index": payload["round_index"],
        "substitution_digest": _digest(payload["sboxes"]),
        "linear_digest": (
            None
            if payload["linear_matrix"] is None
            else _digest(payload["linear_matrix"])
        ),
    }
    if include_components:
        summary["sboxes"] = payload["sboxes"]
        summary["linear_matrix"] = payload["linear_matrix"]
    return summary


def _member_summary(member: Any, position: int, include_components: bool) -> dict[str, Any]:
    summary = {
        field: json_safe(_get(member, field))
        for field in _MEMBER_FIELDS
    }
    summary["position"] = position

    try:
        fingerprint_method = getattr(member, "candidate_fingerprint", None)
        if callable(fingerprint_method):
            summary["fingerprint"] = json_safe(fingerprint_method())
    except Exception as exc:
        summary["fingerprint_error"] = "%s: %s" % (type(exc).__name__, exc)

    round_functions = _get(member, "round_functions", []) or []
    component_payloads = [_component_payload(item) for item in round_functions]
    summary["component_digest"] = _digest(component_payloads)
    summary["rounds"] = [
        _round_summary(item, include_components) for item in round_functions
    ]
    return summary


def _numeric_values(members: list[dict[str, Any]], field: str) -> list[float]:
    values = []
    for member in members:
        value = member.get(field)
        if isinstance(value, Real) and not isinstance(value, bool):
            number = float(value)
            if math.isfinite(number):
                values.append(number)
    return values


def _metric_summary(members: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    values = _numeric_values(members, field)
    if not values:
        return None
    return {
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def population_summary(
    population: Any,
    *,
    include_components: bool = False,
) -> dict[str, Any]:
    """Build a compact, auditable snapshot of a Generation or member iterable."""

    # main.run may pass the already-captured pre-transition summary so the
    # top-level generation number and population snapshot describe the same
    # evaluated iteration even when the in-memory Generation has advanced.
    if (
        isinstance(population, Mapping)
        and "size" in population
        and "members" in population
        and "metrics" in population
    ):
        return json_safe(population)

    generation = population if hasattr(population, "members") else None
    members_source = _get(population, "members", population)
    if members_source is None:
        members = []
    elif isinstance(members_source, Mapping):
        members = list(members_source.values())
    else:
        try:
            members = list(members_source)
        except TypeError:
            members = [members_source]

    member_summaries = [
        _member_summary(member, position, include_components)
        for position, member in enumerate(members)
    ]
    fitness_values = _numeric_values(member_summaries, "fitness")
    best_member = None
    if fitness_values:
        candidates = [
            member
            for member in member_summaries
            if isinstance(member.get("fitness"), Real)
            and not isinstance(member.get("fitness"), bool)
            and math.isfinite(float(member["fitness"]))
        ]
        best = max(candidates, key=lambda member: float(member["fitness"]))
        best_member = {
            "candidate_id": best.get("candidate_id"),
            "identifier": best.get("identifier"),
            "pop_index": best.get("pop_index"),
            "fitness": best.get("fitness"),
            "component_digest": best.get("component_digest"),
        }

    result = {
        "size": len(member_summaries),
        "generation_index": json_safe(_get(generation, "gen_index")),
        "num_rounds": json_safe(_get(generation, "num_rounds")),
        "best_member": best_member,
        "metrics": {
            field: _metric_summary(member_summaries, field)
            for field in ("fitness", "diversity", "latency")
        },
        "members": member_summaries,
    }

    if generation is not None:
        result["selection_sizes"] = {
            name: len(_get(generation, name, []) or [])
            for name in (
                "fittest_population",
                "next_fittest_population",
                "breeding_population",
                "next_members",
            )
        }
    return result


def record_changes(changes: Any = None) -> list[dict[str, Any]]:
    """Normalize explicit mutation/crossover/plugin change reports for logging.

    Each mapping is preserved so external components may extend the schema. A
    plain string becomes a ``description`` and other values are stored under
    ``value``.  ``change_index`` preserves the execution order.
    """

    if changes is None:
        return []
    if isinstance(changes, Mapping) or isinstance(changes, (str, bytes)):
        source: Iterable[Any] = [changes]
    else:
        try:
            source = list(changes)
        except TypeError:
            source = [changes]

    records = []
    for index, change in enumerate(source):
        safe_change = json_safe(change)
        if isinstance(safe_change, dict):
            entry = dict(safe_change)
        elif isinstance(safe_change, str):
            entry = {"description": safe_change}
        else:
            entry = {"value": safe_change}
        entry.setdefault("change_index", index)
        records.append(entry)
    return records


class IterationLogger:
    """Append one self-contained JSON object per completed search generation."""

    def __init__(
        self,
        output: str | os.PathLike[str],
        filename: str = "iterations.jsonl",
        *,
        clock: Any = None,
    ) -> None:
        output_path = Path(output)
        self.path = output_path if output_path.suffix == ".jsonl" else output_path / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._write_lock = threading.Lock()

    def log_generation(
        self,
        generation: Any = None,
        changes: Any = None,
        *,
        population: Any = None,
        generation_index: int | None = None,
        iteration_index: int | None = None,
        num_rounds: int | None = None,
        stage: str = "completed",
        metadata: Mapping[str, Any] | None = None,
        include_components: bool = False,
    ) -> dict[str, Any]:
        """Append and return a generation log record.

        ``generation`` is the normal uKNIT ``Generation`` object.  ``population``
        is an override for callers that keep members separately.
        """

        population_source = population if population is not None else generation
        summary = population_summary(
            population_source,
            include_components=include_components,
        )
        if generation_index is None:
            generation_index = _get(generation, "gen_index", summary["generation_index"])
        if num_rounds is None:
            num_rounds = _get(generation, "num_rounds", summary["num_rounds"])

        timestamp = self._clock()
        timestamp_value = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        entry = {
            "schema_version": 1,
            "event": "generation",
            "timestamp": timestamp_value,
            "stage": stage,
            "iteration_index": iteration_index,
            "generation_index": generation_index,
            "num_rounds": num_rounds,
            "population": summary,
            "changes": record_changes(changes),
            "metadata": json_safe(metadata or {}),
        }
        safe_entry = json_safe(entry)
        line = json.dumps(
            safe_entry,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._write_lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.write("\n")
                handle.flush()
        return safe_entry
