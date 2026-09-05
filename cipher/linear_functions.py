"""
This file contains
class linear_function: contains static methods for the linear layer
"""

import config
import numpy as np
import pickle

from seed_config import SEED, set_global_seed
set_global_seed(SEED)

class linear_functions:
    @staticmethod
    def print_matrix(mat):
        for i in range(len(mat)):
            for j in range(len(mat)):
                print(mat[i][j],end='')
            print()
        print()

    @staticmethod
    def is_valid_index_representation(perms, row_weight=None):
        """Validate the compact representation of each matrix row.

        The historical representation stores one sequence per possible 1;
        sequence ``k`` contains the input-column index for every output row,
        with ``-1`` used only as padding.  Every non-padding index must be an
        integer in ``0..63`` and a row may not repeat an index.
        """
        try:
            sequences = [list(sequence) for sequence in perms]
        except (TypeError, ValueError):
            return False
        if not sequences or any(len(sequence) != 64 for sequence in sequences):
            return False
        if row_weight is not None and len(sequences) != int(row_weight):
            return False
        for row_index in range(64):
            indices = []
            for sequence in sequences:
                value = sequence[row_index]
                if value == -1:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                    return False
                if not 0 <= int(value) < 64:
                    return False
                indices.append(int(value))
            if row_weight is not None and len(indices) != int(row_weight):
                return False
            if len(indices) != len(set(indices)):
                return False
        return True

    @staticmethod
    def list2matrix(perms, row_weight=None): # converting a list type to a matrix
        try:
            perms = [list(sequence) for sequence in perms]
        except (TypeError, ValueError):
            raise ValueError('invalid linear-layer index representation')
        if not linear_functions.is_valid_index_representation(perms, row_weight=row_weight):
            raise ValueError('invalid linear-layer index representation')
        mat = np.zeros((64,64),dtype=int)
        for i in range(64):
            for perm in perms:
                if perm[i] == -1: continue
                mat[63-i,63-perm[i]] = 1
        return mat

    @staticmethod
    def matrix2list(mat):
        mat = np.asarray(mat)
        if mat.shape != (64, 64) or not np.all(np.isin(mat, [0, 1])):
            raise ValueError('matrix must be a binary 64x64 array')
        row_indices = []
        for i in range(64):
            row_indices.append([
                j for j in range(64) if mat[63-i][63-j] == 1
            ])
        max_weight = max((len(indices) for indices in row_indices), default=0)
        perm = [[] for _ in range(max_weight)]
        for indices in row_indices:
            for count in range(max_weight):
                perm[count].append(indices[count] if count < len(indices) else -1)
        result = tuple(perm)
        if result and not linear_functions.is_valid_index_representation(result):
            raise ValueError('matrix produced an invalid index representation')
        return result

    @staticmethod
    def is_invertible(A):
        try:
            linear_functions.inverse(A)
            return True
        except:
            return False

    @staticmethod
    def is_orthogonal(matrix):
        """Check ``M^T M = I`` over GF(2) for a 64-bit binary matrix."""
        try:
            raw_matrix = np.asarray(matrix)
            if raw_matrix.shape != (64, 64) or not np.all(np.isin(raw_matrix, [0, 1])):
                return False
            matrix = raw_matrix.astype(int)
            identity = np.eye(64, dtype=int)
            return bool(np.array_equal((matrix.T.dot(matrix)) % 2, identity))
        except (TypeError, ValueError, IndexError):
            return False

    @staticmethod
    def is_valid_permutation_matrix(matrix, size):
        """Check a binary position-permutation matrix."""
        try:
            matrix = np.asarray(matrix)
            return bool(
                matrix.shape == (size, size)
                and np.all(np.isin(matrix, [0, 1]))
                and np.all(matrix.sum(axis=0) == 1)
                and np.all(matrix.sum(axis=1) == 1)
            )
        except (TypeError, ValueError, IndexError):
            return False

    @staticmethod
    def is_valid_linear_matrix(matrix, row_column_weight=None, require_orthogonal=True):
        """Check the structural contract for a 64-bit binary linear layer.

        ``row_column_weight`` can enforce the sparse uKNIT-BC invariant while
        ``require_orthogonal`` checks the stronger GF(2) contract used by
        uKNIT-BC: ``M^T M = I`` and ``M^-1 M = I``.
        """
        try:
            raw_matrix = np.asarray(matrix)
            valid = bool(
                raw_matrix.shape == (64, 64)
                and np.all(np.isin(raw_matrix, [0, 1]))
            )
            if valid:
                matrix = raw_matrix.astype(int)
                valid = linear_functions.is_invertible(matrix)
            if valid:
                identity = np.eye(64, dtype=int)
                inverse = linear_functions.inverse(matrix) % 2
                valid = bool(np.array_equal((inverse.dot(matrix)) % 2, identity))
                if require_orthogonal:
                    valid = bool(
                        valid
                        and np.array_equal((matrix.T.dot(matrix)) % 2, identity)
                        and np.array_equal(inverse, matrix.T % 2)
                    )
            if valid and row_column_weight is not None:
                valid = bool(
                    np.all(matrix.sum(axis=0) == int(row_column_weight))
                    and np.all(matrix.sum(axis=1) == int(row_column_weight))
                )
            return valid
        except (TypeError, ValueError, IndexError):
            return False
    
    @staticmethod
    def inverse(A): # perform Gaussian elimination
        
        A_new = np.block([np.array(A,dtype=int),np.eye(A.shape[0],dtype=int)])
        for i in range(A.shape[0]):
            # Find pivot row
            pivot = i
            to_swap = None
            for j in range(i, A.shape[0]):
                if A_new[j][i] == 1:
                    to_swap = j
                    break
            if to_swap is None:
                raise Exception('Attempting to find the inverse of a singular matrix!')
        
            # Swap pivot row with current row
            A_new[[pivot,to_swap]] = A_new[[to_swap,pivot]]
            # Eliminate current variable from other rows
            for j in range(pivot+1,A.shape[0]):
                if A_new[j][pivot] == 1:
                    A_new[j] = [a ^ b for a, b in zip(A_new[j], A_new[pivot])]

        # removing
        for i in reversed(range(A.shape[0])):
            for j in range(i):
                if A_new[j][i] == 1: A_new[j] = [a ^ b for a, b in zip(A_new[j], A_new[i])]
        A_new = A_new[:,A.shape[0]:]
        return A_new

    @staticmethod
    def random_swap(mat,num_times=1):
        if not isinstance(mat,np.ndarray): mat = linear_functions.list2matrix(mat)
        for _ in range(num_times):
            index0 = np.random.randint(0,64)
            index1 = np.random.randint(0,64)
            indicator = np.random.randint(0,2) # row or column indicator
            mat_copy = mat.copy()
            if indicator == 0: # swap rows
                mat_copy[index0,:],mat_copy[index1,:] = mat[index1,:],mat[index0,:]
            else:
                mat_copy[:,index0],mat_copy[:,index1] = mat[:,index1],mat[:,index0]
        return mat_copy

    @staticmethod
    def random_block_swaps(mat,num_times=1): # randomizing in blocks
        """Apply block-preserving row/column swaps to a 16x16 base matrix.

        Each iteration chooses one of the four groups of four rows/columns,
        then exchanges two distinct positions inside that group.  Row and
        column weights are therefore preserved exactly.
        """
        if not isinstance(mat,np.ndarray): mat = np.asarray(mat, dtype=int)
        mat = np.asarray(mat, dtype=int).copy()
        if mat.shape != (16, 16):
            raise ValueError('random_block_swaps expects a 16x16 matrix')
        for _ in range(num_times):
            indicator = np.random.randint(0,2) # row or column indicator
            block_index = np.random.randint(0,4)
            index0, index1 = np.random.choice(4, size=2, replace=False)
        
            if indicator == 1: # column, we transpose it first
                mat = mat.T

            # swapping the block (row)
            mat[[block_index * 4 + index0]],mat[[block_index * 4 + index1]] = \
                    mat[[block_index * 4 + index1]],mat[[block_index * 4 + index0]]
            
            if indicator == 1: # column, we transpose it back
                mat = mat.T

        return mat

    @staticmethod
    def rotate_row(row,val,length=64):
        return np.block([[row[length-val:],row[:length-val]]])

    @staticmethod
    def get_aes_shiftrows():
        M = np.eye(64,dtype=int)
        for i in range(4):
            for j in range(4):
                for k in range(4):
                    M[16*i+4*j+k] = linear_functions.rotate_row(M[16*i+4*j+k],16*j)
        if not linear_functions.is_valid_permutation_matrix(M, 64):
            raise ValueError('AES ShiftRows must be a 64x64 permutation matrix')
        return M

    @staticmethod
    def get_aes_invshiftrows():
        M = np.eye(64,dtype=int)
        for i in range(4):
            for j in range(4):
                for k in range(4):
                    M[16*i+4*j+k] = linear_functions.rotate_row(M[16*i+4*j+k],64-16*j)
        if not linear_functions.is_valid_permutation_matrix(M, 64):
            raise ValueError('AES inverse ShiftRows must be a 64x64 permutation matrix')
        return M

    @staticmethod
    def get_midori_shiftrows():
        shift_index = [0,10,5,15,14,4,11,1,9,3,12,6,7,13,2,8]
        M = np.block([[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[0])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[0])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[1])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[1])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[2])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[2])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[3])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[3])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[4])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[4])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[5])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[5])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[6])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[6])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[7])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[7])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[8])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[8])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[9])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[9])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[10])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[10])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[11])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[11])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[12])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[12])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[13])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[13])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[14])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[14])]])],[np.block([[np.zeros((4,4),dtype=int) for _ in range(shift_index[15])] + [np.eye(4,dtype=int)] + [np.zeros((4,4),dtype=int) for _ in range(15-shift_index[15])]])]])
        return M

    @staticmethod
    def get_midori_like_matrix():
        midori_shift0 = np.block([[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)]])
        midori_shift1 = np.block([[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)]])
        midori_shift2 = np.block([[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)]])
        midori_shift3 = np.block([[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)]])
        midori_antishift0 = np.block([[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)]])
        midori_antishift1 = np.block([[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)]])
        midori_antishift2 = np.block([[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)]])
        midori_antishift3 = np.block([[np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int)],[np.eye(4,dtype=int),np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int)],[np.eye(4,dtype=int),np.zeros((4,4),dtype=int),np.eye(4,dtype=int),np.eye(4,dtype=int)]])
        options = [midori_shift0, midori_shift1, midori_shift2, midori_shift3,midori_antishift0, midori_antishift1, midori_antishift2, midori_antishift3]
        return options[np.random.choice(len(options))]

    @staticmethod
    def get_prince_m1():
        return np.array([[0,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0],[0,1,0,0,0,0,0,0,0,1,0,0,0,1,0,0],[0,0,1,0,0,0,1,0,0,0,0,0,0,0,1,0],[0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,0],[1,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0],[0,0,0,0,0,1,0,0,0,1,0,0,0,1,0,0],[0,0,1,0,0,0,0,0,0,0,1,0,0,0,1,0],[0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,1],[1,0,0,0,1,0,0,0,0,0,0,0,1,0,0,0],[0,1,0,0,0,1,0,0,0,1,0,0,0,0,0,0],[0,0,0,0,0,0,1,0,0,0,1,0,0,0,1,0],[0,0,0,1,0,0,0,0,0,0,0,1,0,0,0,1],[1,0,0,0,0,0,0,0,1,0,0,0,1,0,0,0],[0,1,0,0,0,1,0,0,0,0,0,0,0,1,0,0],[0,0,1,0,0,0,1,0,0,0,1,0,0,0,0,0],[0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,1]],dtype=int)

    @staticmethod
    def get_prince_m2():
        return np.array([[1,0,0,0,1,0,0,0,1,0,0,0,0,0,0,0],[0,0,0,0,0,1,0,0,0,1,0,0,0,1,0,0],[0,0,1,0,0,0,0,0,0,0,1,0,0,0,1,0],[0,0,0,1,0,0,0,1,0,0,0,0,0,0,0,1],[1,0,0,0,1,0,0,0,0,0,0,0,1,0,0,0],[0,1,0,0,0,1,0,0,0,1,0,0,0,0,0,0],[0,0,0,0,0,0,1,0,0,0,1,0,0,0,1,0],[0,0,0,1,0,0,0,0,0,0,0,1,0,0,0,1],[1,0,0,0,0,0,0,0,1,0,0,0,1,0,0,0],[0,1,0,0,0,1,0,0,0,0,0,0,0,1,0,0],[0,0,1,0,0,0,1,0,0,0,1,0,0,0,0,0],[0,0,0,0,0,0,0,1,0,0,0,1,0,0,0,1],[0,0,0,0,1,0,0,0,1,0,0,0,1,0,0,0],[0,1,0,0,0,0,0,0,0,1,0,0,0,1,0,0],[0,0,1,0,0,0,1,0,0,0,0,0,0,0,1,0],[0,0,0,1,0,0,0,1,0,0,0,1,0,0,0,0]],dtype=int)

    @staticmethod
    def mutate(mat, return_details=False):
        """Mutate a 64x64 linear layer by swapping rows, then columns.

        Each swap uses two distinct positions.  The configurable counts are
        implementation parameters, but at least one row pair and one column
        pair are exchanged for every mutation event.
        """
        if not isinstance(mat, np.ndarray):
            mat = linear_functions.list2matrix(mat)
        raw_mat = np.asarray(mat)
        if not linear_functions.is_valid_linear_matrix(raw_mat):
            raise ValueError('linear layer must be a valid 64x64 binary invertible matrix')
        mat = np.array(raw_mat, dtype=int, copy=True)

        row_swaps = []
        row_count = max(1, int(config.GENETIC_ALGO['LINEAR']['MAX_ROW_SWAPS']))
        for _ in range(row_count):
            index0, index1 = np.random.choice(64, size=2, replace=False)
            index0, index1 = int(index0), int(index1)
            mat[[index0, index1]] = mat[[index1, index0]]
            row_swaps.append([index0, index1])

        column_swaps = []
        column_count = max(1, int(config.GENETIC_ALGO['LINEAR']['MAX_COL_SWAPS']))
        for _ in range(column_count):
            index0, index1 = np.random.choice(64, size=2, replace=False)
            index0, index1 = int(index0), int(index1)
            mat[:, [index0, index1]] = mat[:, [index1, index0]]
            column_swaps.append([index0, index1])

        if not linear_functions.is_valid_linear_matrix(mat):
            raise ValueError('linear-layer mutation produced an invalid matrix')
        if return_details:
            return mat, {
                'row_swaps': row_swaps,
                'column_swaps': column_swaps,
            }
        return mat

    @staticmethod
    def get_linear():
        mix_config = config.INIT_SETTINGS['PERMUTATION']['MIXCOLUMNS']
        prince_probability = float(mix_config.get('PRINCE_LIKE', 0.0))
        # ``MANTIS_LIKE`` was the old name for the Midori-like branch.  Keep
        # it as a read-only compatibility fallback for older config files.
        midori_probability = float(mix_config.get(
            'MIDORI_LIKE', mix_config.get('MANTIS_LIKE', 0.0)
        ))
        probabilities = np.asarray(
            [prince_probability, midori_probability], dtype=float
        )
        if np.any(probabilities < 0) or probabilities.sum() <= 0:
            raise ValueError('MIXCOLUMNS probabilities must contain a positive value')
        probabilities /= probabilities.sum()

        mat = np.zeros((64,64),dtype=int)
        # Build a block diagonal matrix from independently randomized
        # PRINCE/MIDORI-like 16x16 bases.
        for block_index in range(4):
            chosen_function = np.random.choice(
                ['PRINCE_LIKE', 'MIDORI_LIKE'], p=probabilities
            )
            if chosen_function == 'PRINCE_LIKE':
                base = linear_functions.get_prince_m1()
            else:
                base = linear_functions.get_midori_like_matrix()
            swap_count = int(np.random.randint(1, 1001))
            block = linear_functions.random_block_swaps(base, swap_count)
            if not linear_functions._is_regular_binary_matrix(block, 16, 3):
                raise ValueError('randomized base block must have exactly three 1s per row and column')
            start = 16 * block_index
            mat[start:start+16, start:start+16] = block

        if not linear_functions._is_regular_binary_matrix(mat, 64, 3):
            raise ValueError('block diagonal diffusion matrix is malformed')
        if not linear_functions.is_invertible(mat):
            raise ValueError('block diagonal diffusion matrix is singular')

        # PRINCE uses an AES-like ShiftRows permutation over 4-bit nibbles.
        result = linear_functions.get_aes_shiftrows().dot(mat)
        if not linear_functions._is_regular_binary_matrix(result, 64, 3):
            raise ValueError('ShiftRows must preserve three 1s per row and column')
        if not linear_functions.is_valid_linear_matrix(result, row_column_weight=3):
            raise ValueError(
                'final diffusion matrix must be invertible and orthogonal over GF(2)'
            )
        return result

    @staticmethod
    def _is_regular_binary_matrix(mat, size, weight):
        """Check a binary square matrix with a fixed row/column Hamming weight."""
        mat = np.asarray(mat)
        return bool(
            mat.shape == (size, size)
            and np.all(np.isin(mat, [0, 1]))
            and np.all(mat.sum(axis=0) == weight)
            and np.all(mat.sum(axis=1) == weight)
        )
    
    # for diversity computation
    @staticmethod
    def compute_distance(linearA,linearB):
        mat = linearA.matrix * linearB.matrix
        return 1 - 2 * np.sum(mat) / (np.sum(linearA.matrix) + np.sum(linearB.matrix))

