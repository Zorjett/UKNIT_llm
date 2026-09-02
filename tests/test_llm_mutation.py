import copy
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np

from cipher.Ciphers import Member
import cipher.components as components
from llm_mutation import (
    DeepSeekMutationAdvisor,
    DeepSeekSettings,
    apply_mutation_plan,
    _parse_plan,
)


def _member(label, offset=0):
    member = Member()
    member.gen_index = 0
    member.pop_index = offset
    member.candidate_id = label
    for round_index in range(2):
        round_function = components.round_function()
        substitution = components.substitution_layer()
        for sbox_index in range(16):
            table = [((value + offset + round_index + sbox_index) % 16) for value in range(16)]
            substitution.add_sbox(table)
        round_function.add_substitution_layer(substitution)
        if round_index == 0:
            linear = components.linear_layer()
            linear.matrix = np.eye(64, dtype=int)
            round_function.add_linear_layer(linear)
        else:
            round_function.linear = None
        member.add_round_function(round_function)
    return member


class MutationPlanTests(unittest.TestCase):
    def test_nested_plans_bind_target_id_and_fingerprint(self):
        member = _member("child")
        fingerprint = member.candidate_fingerprint()
        payload = {
            "schema_version": "1.0",
            "request_id": "request-1",
            "generation": 3,
            "plans": [
                {
                    "target_candidate_id": "child",
                    "base_fingerprint": fingerprint,
                    "operations": [
                        {
                            "round_index": 0,
                            "component": "sbox",
                            "operation": "sbox_swap_entries",
                            "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
                            "reason": "test",
                        }
                    ],
                }
            ],
        }
        operations, rejected, rationale = _parse_plan(
            payload,
            request_id="request-1",
            member_count=1,
            max_total_operations=10,
            max_operations_per_candidate=4,
            candidate_bindings={
                "entries": [
                    {
                        "candidate_index": 0,
                        "candidate_id": "child",
                        "fingerprint": fingerprint,
                    }
                ]
            },
            expected_generation=3,
        )
        self.assertEqual(rationale, None)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["candidate_index"], 0)
        self.assertEqual(operations[0]["target_candidate_id"], "child")
        self.assertEqual(operations[0]["base_fingerprint"], fingerprint)

    def test_stale_fingerprint_is_rejected_without_mutation(self):
        member = _member("child")
        original = copy.deepcopy(member)
        operation = {
            "candidate_index": 0,
            "target_candidate_id": "child",
            "base_fingerprint": "stale",
            "round_index": 0,
            "component": "sbox",
            "operation": "sbox_swap_entries",
            "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
            "reason": "test",
        }
        mutated, records = apply_mutation_plan([member], [operation])
        self.assertEqual(records[0]["status"], "rejected")
        self.assertEqual(records[0]["rejection_reason"], "base_fingerprint_mismatch")
        self.assertEqual(mutated[0].candidate_fingerprint(), original.candidate_fingerprint())

    def test_copy_operations_read_immutable_source_snapshot(self):
        target = _member("target", 0)
        source = _member("source", 1)
        source_before = copy.deepcopy(source)
        operations = [
            {
                "candidate_index": 1,
                "target_candidate_id": "source",
                "base_fingerprint": source.candidate_fingerprint(),
                "round_index": 0,
                "component": "sbox",
                "operation": "sbox_swap_entries",
                "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
                "reason": "change source first",
            },
            {
                "candidate_index": 0,
                "target_candidate_id": "target",
                "base_fingerprint": target.candidate_fingerprint(),
                "round_index": 0,
                "component": "copy_component",
                "operation": "copy_component",
                "params": {
                    "source_candidate_index": 1,
                    "source_round_index": 0,
                    "source_component": "sbox",
                },
                "reason": "copy original source",
            },
        ]
        mutated, records = apply_mutation_plan([target, source], operations)
        self.assertEqual([r["status"] for r in records], ["accepted", "accepted"])
        self.assertEqual(
            mutated[0].round_functions[0].substitution.sboxes,
            source_before.round_functions[0].substitution.sboxes,
        )
        self.assertNotEqual(
            mutated[1].round_functions[0].substitution.sboxes[0],
            source_before.round_functions[0].substitution.sboxes[0],
        )

    def test_engineering_failure_rolls_back_all_operations_for_candidate(self):
        member = _member("child")
        original = copy.deepcopy(member)
        operation = {
            "candidate_index": 0,
            "target_candidate_id": "child",
            "base_fingerprint": member.candidate_fingerprint(),
            "round_index": 0,
            "component": "sbox",
            "operation": "sbox_swap_entries",
            "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
            "reason": "must rollback",
        }
        mutated, records = apply_mutation_plan(
            [member],
            [operation],
            engineering_validator=lambda candidate, context=None: {
                "status": "invalid",
                "valid": False,
                "errors": ["rejected by test"],
            },
        )
        self.assertEqual(records[0]["status"], "rejected")
        self.assertEqual(records[0]["rejection_reason"], "engineering_validation_failed")
        self.assertTrue(records[0]["rolled_back"])
        self.assertEqual(mutated[0].candidate_fingerprint(), original.candidate_fingerprint())
        self.assertEqual(records[0]["after_fingerprint"], records[0]["before_fingerprint"])

    def test_engineering_error_status_cannot_be_overridden_by_valid_flag(self):
        member = _member("child")
        original = copy.deepcopy(member)
        operation = {
            "candidate_index": 0,
            "target_candidate_id": "child",
            "base_fingerprint": member.candidate_fingerprint(),
            "round_index": 0,
            "component": "sbox",
            "operation": "sbox_swap_entries",
            "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
            "reason": "error status must win",
        }
        mutated, records = apply_mutation_plan(
            [member],
            [operation],
            engineering_validator=lambda candidate, context=None: {
                "status": "error",
                "valid": True,
                "errors": ["provider failed"],
            },
        )
        self.assertEqual(records[0]["status"], "rejected")
        self.assertEqual(records[0]["rejection_reason"], "engineering_validation_failed")
        self.assertTrue(records[0]["rolled_back"])
        self.assertEqual(mutated[0].candidate_fingerprint(), original.candidate_fingerprint())

    def test_multiple_operations_record_stepwise_fingerprints(self):
        member = _member("child")
        base = member.candidate_fingerprint()
        operations = []
        for entry_a, entry_b in ((0, 1), (2, 3)):
            operations.append(
                {
                    "candidate_index": 0,
                    "target_candidate_id": "child",
                    "base_fingerprint": base,
                    "round_index": 0,
                    "component": "sbox",
                    "operation": "sbox_swap_entries",
                    "params": {
                        "sbox_index": 0,
                        "entry_a": entry_a,
                        "entry_b": entry_b,
                    },
                    "reason": "stepwise",
                }
            )
        mutated, records = apply_mutation_plan([member], operations)
        self.assertEqual([record["status"] for record in records], ["accepted", "accepted"])
        self.assertEqual(records[0]["before_fingerprint"], base)
        self.assertEqual(records[0]["after_fingerprint"], records[1]["before_fingerprint"])
        self.assertEqual(records[1]["after_fingerprint"], mutated[0].candidate_fingerprint())

    def test_direct_plans_payload_resolves_target_id(self):
        member = _member("child")
        fingerprint = member.candidate_fingerprint()
        plan = {
            "plans": [
                {
                    "target_candidate_id": "child",
                    "base_fingerprint": fingerprint,
                    "operations": [
                        {
                            "round_index": 0,
                            "component": "sbox",
                            "operation": "sbox_swap_entries",
                            "params": {"sbox_index": 0, "entry_a": 0, "entry_b": 1},
                            "reason": "target-id only",
                        }
                    ],
                }
            ]
        }
        mutated, records = apply_mutation_plan([member], plan)
        self.assertEqual(records[0]["status"], "accepted")
        self.assertEqual(records[0]["candidate_index"], 0)
        self.assertNotEqual(mutated[0].candidate_fingerprint(), fingerprint)


class DeepSeekTransportTests(unittest.TestCase):
    def test_missing_key_never_calls_http(self):
        member = _member("child")
        called = []

        def urlopen(*args, **kwargs):
            called.append((args, kwargs))
            raise AssertionError("HTTP must not be called")

        advisor = DeepSeekMutationAdvisor(
            DeepSeekSettings(api_key="", model="deepseek-test"), urlopen=urlopen
        )
        mutated, report = advisor.mutate_generation([member])
        self.assertEqual(report["status"], "fallback_noop")
        self.assertEqual(report["fallback_reason"], "missing_api_key")
        self.assertEqual(called, [])
        self.assertEqual(mutated[0].candidate_fingerprint(), member.candidate_fingerprint())

    def test_http_4xx_is_not_retried(self):
        calls = []

        def urlopen(request, timeout):
            calls.append(1)
            raise HTTPError(request.full_url, 400, "bad request", {}, None)

        advisor = DeepSeekMutationAdvisor(
            DeepSeekSettings(api_key="key", model="model", max_retries=3),
            urlopen=urlopen,
        )
        with self.assertRaises(HTTPError):
            advisor._request_plan({"request_id": "x"})
        self.assertEqual(len(calls), 1)

    def test_http_5xx_is_retried(self):
        calls = []
        content = json.dumps(
            {
                "schema_version": "1.0",
                "request_id": "x",
                "operations": [],
            }
        )

        class Response:
            def read(self, limit):
                return json.dumps(
                    {"choices": [{"message": {"content": content}}]}
                ).encode("utf-8")

            def close(self):
                return None

        def urlopen(request, timeout):
            calls.append(1)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "temporary", {}, None)
            return Response()

        advisor = DeepSeekMutationAdvisor(
            DeepSeekSettings(api_key="key", model="model", max_retries=1),
            urlopen=urlopen,
        )
        with patch("llm_mutation.time.sleep"):
            result = advisor._request_plan({"request_id": "x"})
        self.assertEqual(result["operations"], [])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
