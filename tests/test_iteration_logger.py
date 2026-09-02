import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from iteration_logger import (
    IterationLogger,
    json_safe,
    population_summary,
    record_changes,
)


class _Mode(Enum):
    MUTATE = "mutate"


@dataclass
class _Plan:
    mode: _Mode
    target: Path


class _ArrayLike:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


def _round(index, sbox_value, matrix):
    return SimpleNamespace(
        round_index=index,
        substitution=SimpleNamespace(sboxes=[[sbox_value, 1, 2, 3]]),
        linear=None if matrix is None else SimpleNamespace(matrix=_ArrayLike(matrix)),
    )


def _member(index, fitness, identifier=None):
    return SimpleNamespace(
        identifier=identifier,
        pop_index=index,
        gen_index=2,
        num_rounds=2,
        fitness=fitness,
        diversity=0.25 * index,
        latency=100 + index,
        security_diff=[12, 14],
        security_linear=[7],
        round_functions=[
            _round(0, index, [[1, 0], [0, 1]]),
            _round(1, index + 1, None),
        ],
    )


class JsonSafeTests(unittest.TestCase):
    def test_converts_common_non_json_values(self):
        value = {
            "plan": _Plan(_Mode.MUTATE, Path("plans/next.json")),
            "array": _ArrayLike([[1, 2], [3, 4]]),
            "set": {3, 1},
            "invalid_float": math.inf,
            "when": datetime(2026, 8, 31, 12, 30, tzinfo=timezone.utc),
        }

        converted = json_safe(value)

        self.assertEqual(converted["plan"]["mode"], "mutate")
        self.assertEqual(converted["plan"]["target"], os.fspath(Path("plans/next.json")))
        self.assertEqual(converted["array"], [[1, 2], [3, 4]])
        self.assertEqual(converted["set"], [1, 3])
        self.assertIsNone(converted["invalid_float"])
        json.dumps(converted, allow_nan=False)

    def test_handles_recursive_values(self):
        recursive = []
        recursive.append(recursive)

        self.assertEqual(json_safe(recursive), ["<recursive:list>"])


class PopulationSummaryTests(unittest.TestCase):
    def test_summarizes_members_metrics_and_component_state(self):
        generation = SimpleNamespace(
            gen_index=2,
            num_rounds=2,
            members=[_member(0, 1.5), _member(1, 4.5, "elite")],
            fittest_population=[object()],
            next_fittest_population=[],
            breeding_population=[object(), object()],
            next_members=[],
        )

        summary = population_summary(generation)

        self.assertEqual(summary["size"], 2)
        self.assertEqual(summary["generation_index"], 2)
        self.assertEqual(summary["num_rounds"], 2)
        self.assertEqual(summary["metrics"]["fitness"]["mean"], 3.0)
        self.assertEqual(summary["best_member"]["identifier"], "elite")
        self.assertEqual(summary["selection_sizes"]["breeding_population"], 2)
        self.assertEqual(len(summary["members"][0]["component_digest"]), 64)
        self.assertNotIn("sboxes", summary["members"][0]["rounds"][0])

        detailed = population_summary(generation, include_components=True)
        self.assertEqual(detailed["members"][0]["rounds"][0]["sboxes"], [[0, 1, 2, 3]])
        self.assertEqual(
            detailed["members"][0]["rounds"][0]["linear_matrix"],
            [[1, 0], [0, 1]],
        )

    def test_component_digest_changes_when_a_component_changes(self):
        before = population_summary([_member(0, 1.0)])
        changed_member = _member(0, 1.0)
        changed_member.round_functions[0].substitution.sboxes[0][0] = 15
        after = population_summary([changed_member])

        self.assertNotEqual(
            before["members"][0]["component_digest"],
            after["members"][0]["component_digest"],
        )


class ChangeRecordTests(unittest.TestCase):
    def test_normalizes_changes_without_discarding_plugin_fields(self):
        changes = record_changes(
            [
                {"operator": "replace_sbox", "member": 3, "round": 1},
                "child 4 inherited round 2 from parent 9",
            ]
        )

        self.assertEqual(changes[0]["operator"], "replace_sbox")
        self.assertEqual(changes[0]["change_index"], 0)
        self.assertEqual(changes[1]["description"], "child 4 inherited round 2 from parent 9")
        self.assertEqual(changes[1]["change_index"], 1)


class IterationLoggerTests(unittest.TestCase):
    def test_creates_directory_and_appends_valid_jsonl(self):
        generation = SimpleNamespace(
            gen_index=2,
            num_rounds=2,
            members=[_member(0, 1.5)],
        )
        fixed_time = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory) / "nested" / "logs"
            logger = IterationLogger(output_directory, clock=lambda: fixed_time)
            first = logger.log_generation(
                generation,
                [{"operator": "replace_linear", "status": "applied"}],
                iteration_index=7,
                metadata={"model": "deepseek-chat", "note": "下一代"},
            )
            logger.log_generation(
                generation,
                [],
                iteration_index=8,
                stage="selected",
            )

            self.assertTrue(logger.path.is_file())
            lines = logger.path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            decoded = [json.loads(line) for line in lines]
            self.assertEqual(decoded[0], first)
            self.assertEqual(decoded[0]["iteration_index"], 7)
            self.assertEqual(decoded[0]["generation_index"], 2)
            self.assertEqual(decoded[0]["population"]["size"], 1)
            self.assertEqual(decoded[0]["changes"][0]["operator"], "replace_linear")
            self.assertEqual(decoded[0]["metadata"]["note"], "下一代")
            self.assertEqual(decoded[1]["stage"], "selected")


if __name__ == "__main__":
    unittest.main()
