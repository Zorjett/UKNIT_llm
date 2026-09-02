import unittest
from copy import deepcopy
from unittest.mock import patch

import config
import cipher.Ciphers as ciphers
from cipher.Ciphers import Generation


class _ImmediateFuture:
    def __init__(self, function, *args):
        self._value = function(*args)

    def result(self):
        return self._value


class _ImmediateExecutor:
    """Small test double that keeps next_gen in-process on Windows."""

    def __init__(self, max_workers=None):
        self.max_workers = max_workers

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def submit(self, function, *args):
        return _ImmediateFuture(function, *args)


class RoundGrowthFallbackTests(unittest.TestCase):
    def test_plugin_mode_downgrades_steal_without_trails(self):
        old_mode = config.FRAMEWORK["EVALUATION_MODE"]
        old_generations = list(config.HYPERPARAMETERS["MAX_GENERATION"])
        old_max_rounds = config.HYPERPARAMETERS["MAX_NUM_ROUNDS"]
        old_add_one_round = dict(config.GENETIC_ALGO["ADD_ONE_ROUND"])
        try:
            config.FRAMEWORK["EVALUATION_MODE"] = "plugins"
            # Force the next_gen call into its round-growth branch.
            config.HYPERPARAMETERS["MAX_GENERATION"] = [0, 0, 0]
            config.HYPERPARAMETERS["MAX_NUM_ROUNDS"] = 2
            config.GENETIC_ALGO["ADD_ONE_ROUND"] = {"STEAL": 1.0, "RANDOM": 0.0}

            generation = Generation(1, 0)
            generation.randomize(2)
            for member in generation.members:
                member.diff_trails = None
                member.linear_trails = None

            with patch("cipher.Ciphers.ProcessPoolExecutor", _ImmediateExecutor):
                self.assertEqual(generation.next_gen(max_threads=1), 1)

            self.assertEqual(generation.num_rounds, 2)
            self.assertEqual([member.num_rounds for member in generation.members], [2, 2])
            report = generation.last_round_growth_report
            self.assertEqual(report["requested_choices"], ["STEAL", "STEAL"])
            self.assertEqual(report["effective_choices"], ["RANDOM", "RANDOM"])
            self.assertEqual(len(report["fallbacks"]), 2)
            self.assertTrue(all(item["reason"] == "missing_diff_or_linear_trails" for item in report["fallbacks"]))
        finally:
            config.FRAMEWORK["EVALUATION_MODE"] = old_mode
            config.HYPERPARAMETERS["MAX_GENERATION"] = old_generations
            config.HYPERPARAMETERS["MAX_NUM_ROUNDS"] = old_max_rounds
            config.GENETIC_ALGO["ADD_ONE_ROUND"] = old_add_one_round


class RoundGrowthOrderingTests(unittest.TestCase):
    def test_keeps_member_order_and_choice_mapping_while_resetting_metrics(self):
        old_mode = config.FRAMEWORK["EVALUATION_MODE"]
        old_generations = list(config.HYPERPARAMETERS["MAX_GENERATION"])
        old_max_rounds = config.HYPERPARAMETERS["MAX_NUM_ROUNDS"]
        old_population_size = config.HYPERPARAMETERS["POPULATION_SIZE"]
        old_add_one_round = dict(config.GENETIC_ALGO["ADD_ONE_ROUND"])
        try:
            config.FRAMEWORK["EVALUATION_MODE"] = "plugins"
            config.HYPERPARAMETERS["MAX_GENERATION"] = [0, 0, 0]
            config.HYPERPARAMETERS["MAX_NUM_ROUNDS"] = 2
            config.HYPERPARAMETERS["POPULATION_SIZE"] = 3
            config.GENETIC_ALGO["ADD_ONE_ROUND"] = {"STEAL": 0.5, "RANDOM": 0.5}

            generation = Generation(1, 0)
            generation.randomize(3)
            for index, member in enumerate(generation.members):
                member.candidate_id = "source-%s" % index
                member.fitness = 3 - index
                member.security_diff = [10]
                member.security_linear = [8]
                member.latency = 10.0
                member.diversity = 2.0
                member.plugin_security = {"status": "ok"}
                member.plugin_validation = {"valid": True}
                member.plugin_performance = {"status": "ok"}
                member.evaluation_status = "ok"

            def _tagged_member(member, marker):
                result = deepcopy(member)
                result.marker = marker
                result.num_rounds += 1
                return result

            with patch.object(
                ciphers.np.random,
                "choice",
                return_value=ciphers.np.asarray(["STEAL", "RANDOM", "STEAL"], dtype=object),
            ), patch.object(
                Generation, "_can_steal_one_round", return_value=True
            ), patch(
                "cipher.Ciphers.ProcessPoolExecutor", _ImmediateExecutor
            ), patch(
                "cipher.Ciphers.utils.call_steal_one_round",
                side_effect=lambda member, members: _tagged_member(member, "STEAL"),
            ), patch(
                "cipher.Ciphers.utils.smart_randomize_one_round",
                side_effect=lambda member: _tagged_member(member, "RANDOM"),
            ):
                self.assertEqual(generation.next_gen(max_threads=1), 1)

            self.assertEqual(
                [member.marker for member in generation.members],
                ["STEAL", "RANDOM", "STEAL"],
            )
            report = generation.last_round_growth_report
            self.assertEqual(report["requested_choices"], ["STEAL", "RANDOM", "STEAL"])
            self.assertEqual(report["effective_choices"], ["STEAL", "RANDOM", "STEAL"])
            self.assertEqual(
                [item["source_member_id"] for item in report["members"]],
                ["source-0", "source-1", "source-2"],
            )
            self.assertEqual(
                [item["effective"] for item in report["members"]],
                ["STEAL", "RANDOM", "STEAL"],
            )
            self.assertEqual(generation.num_member, 3)
            for member in generation.members:
                self.assertEqual(member.evaluation_status, "pending")
                self.assertIsNone(member.fitness)
                self.assertIsNone(member.diversity)
                self.assertIsNone(member.security_diff)
                self.assertIsNone(member.security_linear)
                self.assertIsNone(member.latency)
                self.assertIsNone(member.plugin_security)
                self.assertIsNone(member.plugin_validation)
                self.assertIsNone(member.plugin_performance)
        finally:
            config.FRAMEWORK["EVALUATION_MODE"] = old_mode
            config.HYPERPARAMETERS["MAX_GENERATION"] = old_generations
            config.HYPERPARAMETERS["MAX_NUM_ROUNDS"] = old_max_rounds
            config.HYPERPARAMETERS["POPULATION_SIZE"] = old_population_size
            config.GENETIC_ALGO["ADD_ONE_ROUND"] = old_add_one_round


if __name__ == "__main__":
    unittest.main()
