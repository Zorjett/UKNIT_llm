import unittest
from unittest.mock import patch

import config
from cipher.Ciphers import Member
import cipher.components as components


def _one_round_member():
    member = Member()
    member.gen_index = 0
    member.pop_index = 0
    member.candidate_id = 'plugin-fitness-test'

    round_function = components.round_function()
    substitution = components.substitution_layer()
    for _ in range(16):
        substitution.add_sbox(list(range(16)))
    round_function.add_substitution_layer(substitution)
    round_function.linear = None
    member.add_round_function(round_function)
    return member


class PluginFitnessGateTests(unittest.TestCase):
    def test_performance_valid_false_forces_neutral_fitness(self):
        member = _one_round_member()
        old_mode = config.FRAMEWORK['EVALUATION_MODE']
        try:
            config.FRAMEWORK['EVALUATION_MODE'] = 'plugins'
            with patch(
                'team_plugins.plugin_loader.evaluate_security',
                return_value={
                    'status': 'ok',
                    'ok': True,
                    'differential': {'weights': [10]},
                    'linear': {'weights': [5]},
                },
            ), patch(
                'team_plugins.plugin_loader.validate_candidate',
                return_value={'status': 'ok', 'valid': True},
            ), patch(
                'team_plugins.plugin_loader.evaluate_performance',
                return_value={
                    'status': 'ok',
                    'valid': False,
                    'metrics': {'latency': 10},
                },
            ):
                member.compute_fitness()

            self.assertEqual(member.fitness, 0.0)
            self.assertEqual(member.evaluation_status, 'invalid')
        finally:
            config.FRAMEWORK['EVALUATION_MODE'] = old_mode

    def test_security_ok_false_forces_neutral_fitness(self):
        member = _one_round_member()
        old_mode = config.FRAMEWORK['EVALUATION_MODE']
        try:
            config.FRAMEWORK['EVALUATION_MODE'] = 'plugins'
            with patch(
                'team_plugins.plugin_loader.evaluate_security',
                return_value={
                    'status': 'ok',
                    'ok': False,
                    'differential': {'weights': [10]},
                    'linear': {'weights': [5]},
                },
            ), patch(
                'team_plugins.plugin_loader.validate_candidate',
                return_value={'status': 'ok', 'valid': True},
            ), patch(
                'team_plugins.plugin_loader.evaluate_performance',
                return_value={
                    'status': 'ok',
                    'valid': True,
                    'metrics': {'latency': 10},
                },
            ):
                member.compute_fitness()

            self.assertEqual(member.fitness, 0.0)
            self.assertEqual(member.evaluation_status, 'invalid')
        finally:
            config.FRAMEWORK['EVALUATION_MODE'] = old_mode


if __name__ == '__main__':
    unittest.main()
