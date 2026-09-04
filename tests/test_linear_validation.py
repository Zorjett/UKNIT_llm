import unittest

import numpy as np

from cipher.linear_functions import linear_functions


class LinearValidationTests(unittest.TestCase):
    def test_linear_matrix_validation_checks_orthogonality_and_inverse(self):
        matrix = np.eye(64, dtype=int)
        matrix[0, 1] = 1  # invertible over GF(2), but not orthogonal

        self.assertFalse(linear_functions.is_valid_linear_matrix(matrix))
        self.assertTrue(
            linear_functions.is_valid_linear_matrix(
                matrix, require_orthogonal=False
            )
        )

    def test_linear_matrix_validation_rejects_non_binary_values(self):
        matrix = np.eye(64, dtype=float)
        matrix[0, 1] = 0.5
        self.assertFalse(linear_functions.is_valid_linear_matrix(matrix))

    def test_shiftrows_and_index_representation_are_valid(self):
        shiftrows = linear_functions.get_aes_shiftrows()
        inverse_shiftrows = linear_functions.get_aes_invshiftrows()
        identity = np.eye(64, dtype=int)

        self.assertTrue(linear_functions.is_valid_permutation_matrix(shiftrows, 64))
        self.assertTrue(
            np.array_equal((shiftrows.dot(inverse_shiftrows)) % 2, identity)
        )

        matrix = linear_functions.get_linear()
        inverse = linear_functions.inverse(matrix)
        self.assertTrue(
            np.array_equal((inverse.dot(matrix)) % 2, identity)
        )
        self.assertTrue(np.array_equal(inverse % 2, matrix.T % 2))
        compact = linear_functions.matrix2list(matrix)
        self.assertTrue(
            linear_functions.is_valid_index_representation(compact, row_weight=3)
        )
        self.assertTrue(
            np.array_equal(linear_functions.list2matrix(compact, row_weight=3), matrix)
        )

        invalid = [list(range(64)), list(range(64)), list(range(64))]
        invalid[0][0] = 64
        self.assertFalse(
            linear_functions.is_valid_index_representation(invalid, row_weight=3)
        )


if __name__ == '__main__':
    unittest.main()
