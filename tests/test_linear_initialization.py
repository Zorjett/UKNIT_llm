import unittest
from unittest.mock import patch

import config
from cipher.linear_functions import linear_functions


class LinearInitializationTests(unittest.TestCase):
    def test_config_exposes_prince_and_midori_choices(self):
        choices = config.INIT_SETTINGS['PERMUTATION']['MIXCOLUMNS']
        self.assertEqual(set(choices), {'PRINCE_LIKE', 'MIDORI_LIKE'})
        self.assertAlmostEqual(sum(choices.values()), 1.0)

    def test_random_swap_count_is_one_through_one_thousand_per_block(self):
        original = linear_functions.random_block_swaps
        observed = []

        def capture(matrix, num_times=1):
            observed.append(int(num_times))
            return original(matrix, num_times)

        with patch.object(linear_functions, 'random_block_swaps', side_effect=capture):
            linear_functions.get_linear()

        self.assertEqual(len(observed), 4)
        self.assertTrue(all(1 <= value <= 1000 for value in observed))

    def test_final_matrix_is_binary_sparse_and_invertible(self):
        matrix = linear_functions.get_linear()
        self.assertTrue(linear_functions._is_regular_binary_matrix(matrix, 64, 3))
        self.assertTrue(linear_functions.is_invertible(matrix))


if __name__ == '__main__':
    unittest.main()
