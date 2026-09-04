import unittest
from unittest.mock import patch

import numpy as np

import cipher.Ciphers as ciphers
import cipher.components as components
import cipher.linear_functions as linear_module
from cipher.linear_functions import linear_functions


class LinearMutationTests(unittest.TestCase):
    def test_mutation_swaps_distinct_rows_then_columns_and_preserves_structure(self):
        matrix = linear_functions.get_linear()
        selected_pairs = [np.array([0, 1]), np.array([2, 3])]

        with patch.object(
            linear_module.np.random, 'choice', side_effect=selected_pairs
        ):
            mutated, details = linear_functions.mutate(matrix, return_details=True)

        self.assertEqual(details['row_swaps'], [[0, 1]])
        self.assertEqual(details['column_swaps'], [[2, 3]])
        self.assertTrue(
            linear_functions.is_valid_linear_matrix(mutated, row_column_weight=3)
        )

    def test_member_mutation_can_select_linear_layer(self):
        member = ciphers.Member()
        for round_index in range(2):
            round_function = components.round_function()
            substitution = components.substitution_layer()
            for _ in range(16):
                substitution.add_sbox(list(range(16)))
            round_function.add_substitution_layer(substitution)
            if round_index == 0:
                linear = components.linear_layer()
                linear.matrix = linear_functions.get_linear()
                round_function.add_linear_layer(linear)
            else:
                round_function.linear = None
            member.add_round_function(round_function)

        with patch.object(ciphers.np.random, 'uniform', return_value=0.0), patch.object(
            ciphers.np.random, 'randint', side_effect=[0, 1]
        ), patch.object(
            linear_module.np.random, 'choice',
            side_effect=[np.array([0, 1]), np.array([2, 3])],
        ):
            mutation = member.mutate(prob=0.05)

        self.assertEqual(mutation['round_index'], 0)
        self.assertEqual(mutation['mutation']['component'], 'linear')
        self.assertTrue(
            linear_functions.is_valid_linear_matrix(
                member.round_functions[0].linear.matrix,
                row_column_weight=3,
            )
        )


if __name__ == '__main__':
    unittest.main()
