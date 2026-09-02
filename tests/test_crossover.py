import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cipher.Ciphers as ciphers
import cipher.components as components


def _member(label, offset):
    member = ciphers.Member()
    member.gen_index = 4
    member.pop_index = offset
    member.identifier = label
    member.candidate_id = "candidate-" + label
    member.fitness = 12.0
    member.diversity = 3.0
    member.security_diff = [10]
    member.security_linear = [8]
    member.diff_trails = ["diff"]
    member.linear_trails = ["linear"]
    member.latency = 99.0
    member.evaluation_status = "ok"
    member.evaluation_error = None
    member.plugin_security = {"status": "ok"}
    member.plugin_validation = {"valid": True}
    member.plugin_performance = {"status": "ok"}
    member.mutation_changes = [{"operation": "old"}]

    for round_index in range(3):
        round_function = components.round_function()
        substitution = components.substitution_layer()
        for sbox_index in range(16):
            table = [
                (value + offset + round_index + sbox_index) % 16
                for value in range(16)
            ]
            substitution.add_sbox(table)
        round_function.add_substitution_layer(substitution)
        if round_index < 2:
            round_function.linear = SimpleNamespace(
                matrix="%s-linear-%s" % (label, round_index)
            )
        else:
            round_function.linear = None
        member.add_round_function(round_function)
    return member


class CrossoverMetadataTests(unittest.TestCase):
    def setUp(self):
        self.parent_a = _member("A", 0)
        self.parent_b = _member("B", 1)

    def _assert_child_is_reset(self, child):
        self.assertEqual(child.evaluation_status, "pending")
        for field in (
            "security_diff",
            "diff_trails",
            "security_linear",
            "linear_trails",
            "latency",
            "fitness",
            "diversity",
            "evaluation_error",
            "plugin_security",
            "plugin_validation",
            "plugin_performance",
        ):
            self.assertIsNone(getattr(child, field))
        self.assertEqual(child.mutation_changes, [])

    def test_single_crossover_records_component_sources_and_resets_metrics(self):
        with patch.object(ciphers.np.random, "choice", return_value="SINGLE"), patch.object(
            ciphers.np.random, "randint", return_value=2
        ):
            child_a, child_b = self.parent_a.breed(self.parent_b)

        self.assertEqual(child_a.parent_ids, ["candidate-A", "candidate-B"])
        self.assertEqual(child_a.crossover_strategy, "SINGLE")
        self.assertEqual(child_a.crossover_details["cuts"], [2])
        sources = child_a.crossover_details["component_sources"]
        self.assertEqual(
            [entry["source"] for entry in sources],
            ["parent_a", "parent_a", "parent_b", "parent_b", "parent_b", "parent_b"],
        )
        self.assertEqual(
            [entry["source_id"] for entry in sources],
            ["candidate-A", "candidate-A", "candidate-B", "candidate-B", "candidate-B", "candidate-B"],
        )
        self.assertEqual(child_a.round_functions[0].substitution.sboxes[0][0], 0)
        self.assertEqual(child_a.round_functions[1].substitution.sboxes[0][0], 2)
        self.assertEqual(child_b.round_functions[0].substitution.sboxes[0][0], 1)
        self.assertEqual(child_b.round_functions[1].substitution.sboxes[0][0], 1)
        self._assert_child_is_reset(child_a)
        self._assert_child_is_reset(child_b)
        self.assertEqual(self.parent_a.evaluation_status, "ok")
        self.assertEqual(self.parent_a.fitness, 12.0)

    def test_double_crossover_records_two_cut_segments(self):
        with patch.object(
            ciphers.np.random,
            "choice",
            side_effect=["DOUBLE", [4, 1]],
        ):
            child_a, child_b = self.parent_a.breed(self.parent_b)

        self.assertEqual(child_a.crossover_details["cuts"], [1, 4])
        self.assertEqual(
            [entry["source"] for entry in child_a.crossover_details["component_sources"]],
            ["parent_a", "parent_b", "parent_b", "parent_b", "parent_a", "parent_a"],
        )
        self.assertEqual(
            [entry["source"] for entry in child_b.crossover_details["component_sources"]],
            ["parent_b", "parent_a", "parent_a", "parent_a", "parent_b", "parent_b"],
        )
        self._assert_child_is_reset(child_a)
        self._assert_child_is_reset(child_b)

    def test_uniform_crossover_records_per_sbox_and_linear_sources(self):
        with patch.object(ciphers.np.random, "choice", return_value="UNIFORM"), patch.object(
            ciphers.np.random, "randint", return_value=0
        ):
            child_a, child_b = self.parent_a.breed(self.parent_b)

        rounds = child_a.crossover_details["rounds"]
        self.assertEqual(len(rounds), 3)
        self.assertTrue(all(item["linear_source"] == "parent_a" for item in rounds))
        self.assertTrue(
            all(source == "parent_a" for item in rounds for source in item["sbox_sources"])
        )
        self.assertEqual(child_a.round_functions[1].substitution.sboxes[0][0], 1)
        self.assertEqual(child_b.round_functions[1].substitution.sboxes[0][0], 2)
        self._assert_child_is_reset(child_a)
        self._assert_child_is_reset(child_b)

    def test_single_round_crossover_uses_safe_degenerate_cut(self):
        parent_a = _member("A", 0)
        parent_b = _member("B", 1)
        parent_a.round_functions = parent_a.round_functions[:1]
        parent_b.round_functions = parent_b.round_functions[:1]
        parent_a.num_rounds = parent_b.num_rounds = 1
        parent_a.round_functions[0].linear = None
        parent_b.round_functions[0].linear = None

        for strategy in ("SINGLE", "DOUBLE"):
            with self.subTest(strategy=strategy), patch.object(
                ciphers.np.random, "choice", return_value=strategy
            ):
                child_a, child_b = parent_a.breed(parent_b)
            self.assertEqual(len(child_a.round_functions), 1)
            self.assertEqual(len(child_b.round_functions), 1)
            self.assertIsNone(child_a.round_functions[0].linear)
            self.assertIsNone(child_b.round_functions[0].linear)
            self.assertEqual(child_a.crossover_details["cuts"], [1])
            self._assert_child_is_reset(child_a)


class BreedingContextTests(unittest.TestCase):
    def test_advisor_receives_actual_crossover_children_and_duplicate_hints(self):
        old_population_size = ciphers.config.HYPERPARAMETERS['POPULATION_SIZE']
        try:
            ciphers.config.HYPERPARAMETERS['POPULATION_SIZE'] = 2
            generation = ciphers.Generation(1, 0)
            generation.randomize(2)
            generation.breeding_population = list(generation.members)

            class CaptureAdvisor:
                def __init__(self):
                    self.context = None

                def mutate_generation(self, members, generation_context=None, engineering_validator=None):
                    del engineering_validator
                    self.context = generation_context
                    return list(members), {
                        'status': 'fallback_noop',
                        'fallback_reason': 'test',
                        'change_records': [],
                    }

            advisor = CaptureAdvisor()
            generation.breeding(advisor=advisor)

            self.assertIsNotNone(advisor.context)
            self.assertEqual(
                len(advisor.context['crossover_children']),
                len(generation.next_members),
            )
            self.assertEqual(
                [item['candidate_id'] for item in advisor.context['crossover_children']],
                [member.candidate_id for member in generation.next_members],
            )
            self.assertEqual(
                [item['parent_ids'] for item in advisor.context['crossover_children']],
                [member.parent_ids for member in generation.next_members],
            )
            self.assertIn('details', advisor.context['crossover_children'][0])
            self.assertIn('duplicate_children', advisor.context)
            self.assertEqual(
                advisor.context['crossover_records'],
                generation.last_breeding_records,
            )
        finally:
            ciphers.config.HYPERPARAMETERS['POPULATION_SIZE'] = old_population_size


if __name__ == "__main__":
    unittest.main()
