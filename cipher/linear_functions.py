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
    def list2matrix(perms): # converting a list type to a matrix
        mat = np.zeros((64,64),dtype=int)
        for i in range(64):
            for perm in perms:
                if perm[i] == -1: continue
                mat[63-i,63-perm[i]] = 1
        return mat

    @staticmethod
    def matrix2list(mat):
        perm = [[] for _ in range(64)]
        for i in range(64):
            count = 0
            for j in range(64):
                if M[63-i][63-j] == 1:
                    perm[count].append(j)
                    count += 1
            while count < 64: 
                perm[count].append(-1)
                count += 1
        for i in range(64):
            if sum(perm[i]) == -64: 
                perm = perm[:i]
                break
        return tuple(perm)

    @staticmethod
    def is_invertible(A):
        try:
            linear_functions.inverse(A)
            return True
        except:
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
        if not isinstance(mat,np.ndarray): mat = linear_functions.list2matrix(mat)
        for _ in range(num_times):
            indicator = np.random.randint(0,2) # row or column indicator
            block_index = np.random.randint(0,4)
            index0 = np.random.randint(0,4)
            index1 = np.random.randint(0,4)
        
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

    def get_aes_shiftrows():
        M = np.eye(64,dtype=int)
        for i in range(4):
            for j in range(4):
                for k in range(4):
                    M[16*i+4*j+k] = linear_functions.rotate_row(M[16*i+4*j+k],16*j)
        return M

    @staticmethod
    def get_aes_invshiftrows():
        M = np.eye(64,dtype=int)
        for i in range(4):
            for j in range(4):
                for k in range(4):
                    M[16*i+4*j+k] = linear_functions.rotate_row(M[16*i+4*j+k],64-16*j)
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
    def mutate(mat):
        if not isinstance(mat,np.ndarray):
            mat = linear_functions.list2matrix(mat)
        for _ in range(config.GENETIC_ALGO['LINEAR']['MAX_ROW_SWAPS']):
            index0 = np.random.randint(0,64)
            index1 = np.random.randint(0,64)
            mat[[index1,index0]] = mat[[index0,index1]]

        for _ in range(config.GENETIC_ALGO['LINEAR']['MAX_COL_SWAPS']):
            index0 = np.random.randint(0,64)
            index1 = np.random.randint(0,64)
            mat[:, [index0, index1]] = mat[:, [index1, index0]]
        return mat

    @staticmethod
    def get_linear():
        while True:
            mat = np.zeros((64,64),dtype=int)
            # we choose the 4 different 16x16 matrices
            for block_index in range(4):
                probabilities = [config.INIT_SETTINGS['PERMUTATION']['MIXCOLUMNS']['PRINCE_LIKE'],config.INIT_SETTINGS['PERMUTATION']['MIXCOLUMNS']['MANTIS_LIKE']]
                chosen_function = np.random.choice(['PRINCE_LIKE','MANTIS_LIKE'], p=probabilities)
                if chosen_function == 'PRINCE_LIKE':
                    mat[16*block_index:16*block_index+16,16*block_index:16*block_index+16] = linear_functions.random_block_swaps(linear_functions.get_prince_m1(),1000)
                elif chosen_function == 'MANTIS_LIKE':
                    mat[16*block_index:16*block_index+16,16*block_index:16*block_index+16] = linear_functions.random_block_swaps(linear_functions.get_midori_like_matrix(),1000)

            if linear_functions.is_invertible(mat): 
                return linear_functions.get_aes_shiftrows().dot(mat)
    
    # for diversity computation
    @staticmethod
    def compute_distance(linearA,linearB):
        mat = linearA.matrix * linearB.matrix
        return 1 - 2 * np.sum(mat) / (np.sum(linearA.matrix) + np.sum(linearB.matrix))

