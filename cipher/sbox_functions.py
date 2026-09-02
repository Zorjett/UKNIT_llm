"""
This file contains
class sbox_function: contains static methods for the substitution layers
"""

import config
from cipher.linear_functions import *
import numpy as np    

from seed_config import SEED, set_global_seed
set_global_seed(SEED)

# count_active_sbox
class sbox_functions:
    # predefined good classes of sboxes
    good_sboxes = [[4, 0, 1, 15, 2, 11, 6, 7, 3, 9, 10, 5, 12, 13, 14, 8], [8, 0, 1, 12, 2, 5, 6, 9, 4, 3, 10, 11, 7, 13, 14, 15], [8, 0, 1, 12, 15, 5, 6, 7, 4, 3, 10, 11, 9, 13, 14, 2], [2, 0, 1, 8, 3, 13, 6, 7, 4, 9, 10, 5, 12, 11, 14, 15], [2, 0, 1, 8, 3, 15, 6, 7, 4, 9, 5, 11, 12, 13, 14, 10], [2, 0, 1, 8, 3, 11, 6, 7, 4, 9, 10, 15, 12, 13, 14, 5], [4, 8, 1, 2, 3, 11, 6, 7, 0, 9, 10, 14, 12, 13, 5, 15], [8, 0, 1, 9, 2, 5, 13, 7, 4, 6, 10, 11, 12, 3, 14, 15], [8, 14, 1, 2, 3, 5, 6, 7, 4, 12, 10, 11, 9, 13, 0, 15], [8, 14, 1, 2, 3, 5, 6, 7, 4, 9, 15, 11, 12, 13, 0, 10], [8, 15, 1, 2, 3, 5, 12, 7, 4, 9, 10, 11, 6, 13, 14, 0], [8, 15, 1, 2, 3, 5, 6, 13, 4, 9, 10, 11, 12, 7, 14, 0], [12, 0, 1, 9, 3, 5, 4, 7, 6, 2, 10, 11, 8, 13, 14, 15], [12, 11, 1, 2, 3, 5, 4, 7, 6, 9, 10, 0, 8, 13, 14, 15], [12, 9, 1, 2, 3, 5, 4, 7, 6, 0, 10, 11, 8, 13, 14, 15], [8, 14, 1, 2, 3, 5, 4, 7, 6, 9, 10, 0, 12, 13, 11, 15], [8, 15, 1, 2, 3, 12, 6, 7, 4, 9, 10, 11, 5, 13, 14, 0], [8, 0, 1, 12, 2, 5, 11, 7, 4, 9, 10, 6, 3, 13, 14, 15], [8, 0, 1, 12, 2, 5, 13, 7, 4, 9, 10, 11, 3, 6, 14, 15], [12, 0, 1, 2, 3, 15, 6, 7, 4, 9, 10, 11, 8, 13, 14, 5], [12, 0, 1, 2, 3, 5, 6, 13, 4, 9, 10, 11, 8, 7, 14, 15]]
    ############ basic sbox functions ############
    @staticmethod
    def get_random_vector(size=4):
        return np.random.randint(2, size=size,dtype=bool)

    @staticmethod
    def get_random_bit_permutation(size=4):
        perm = np.random.permutation(size)
        matrix = np.zeros((size, size), dtype=int)
        matrix[np.arange(size), perm] = 1
        return matrix
    
    @staticmethod
    def get_random_linear_permutation(size=4):
        while True:
            mat = np.random.randint(0, 2, size=(size, size), dtype=np.uint8)
            if linear_functions.is_invertible(mat):
                return mat
    
    @staticmethod
    def bin2int(x,size=4):
        res = 0
        for i in range(size):
            res = res << 1
            res = res ^ x[i]
        return res

    @staticmethod
    def int2bin(x,size=4):
        res = []
        for i in range(size):
            res.append((x >> (size-i-1))& 1)
        return res
    
    @staticmethod
    def get_ddt(sbox):
        DDT = np.zeros((len(sbox),len(sbox)),dtype=int)
        for i in range(len(sbox)):
            for j in range(len(sbox)):
                DDT[i^j,sbox[i]^sbox[j]] += 1
        return DDT

    @staticmethod
    def get_lat(sbox):
        LAT = np.zeros((len(sbox),len(sbox)),dtype=int)
        for lin in range(len(sbox)):
            for lout in range(len(sbox)):
                for i in range(len(sbox)):
                    LAT[lin,lout] += (bin((lin & i) ^ (lout & sbox[i])).count('1') % 2 == 0)
                LAT[lin,lout] -= len(sbox) // 2
        return LAT

    @staticmethod
    def count_one_to_one_transitions(sbox): # count the number of one-bit to one-bit transitions for the difference
        DDT = sbox_functions.get_ddt(sbox)
        counter = 0
        for i in range(4):
            for j in range(4):
                if DDT[1 << i,1 << j] > 0:
                    counter += 1
        return counter
    
    ############ sbox mutation ############
    @staticmethod
    def swap_entries(sbox):
        i = np.random.randint(0,len(sbox))
        j = np.random.randint(0,len(sbox))
        sbox[i],sbox[j] = sbox[j],sbox[i]
        return sbox
    
    @staticmethod
    def matrix_transformation_front(A,sbox): # performing a linear transformation on the input
        transformed_sbox = []
        for inp in range(len(sbox)):
            element = sbox_functions.int2bin(inp)
            transformed_element = (A.dot(element) % 2).tolist()
            transformed_element = sbox_functions.bin2int(transformed_element)
            transformed_sbox.append(sbox[transformed_element])
        return transformed_sbox
    
    @staticmethod
    def matrix_transformation_back(A,sbox): # performing a linear transformation on the output
        transformed_sbox = []
        for inp in range(len(sbox)):
            element = sbox_functions.int2bin(sbox[inp])
            transformed_element = (A.dot(element) % 2).tolist()
            transformed_element = sbox_functions.bin2int(transformed_element)
            transformed_sbox.append(transformed_element)
        return transformed_sbox
    
    @staticmethod
    def translation(a,sbox):
        for i in range(len(sbox)):
            sbox[i] = sbox[i] ^ a
        return sbox

    @staticmethod
    def affine_transformation_front(A,a,sbox):
        return sbox_functions.translation(a,sbox_functions.matrix_transformation_front(A,sbox))
    
    @staticmethod
    def affine_transformation_back(A,a,sbox):
        return sbox_functions.translation(a,sbox_functions.matrix_transformation_back(A,sbox))

    @staticmethod
    def mutate(sbox,init=False):
        probabilities = [config.GENETIC_ALGO['SBOX']['AFFINE'],config.GENETIC_ALGO['SBOX']['LINEAR'],config.GENETIC_ALGO['SBOX']['BITPERM'],config.GENETIC_ALGO['SBOX']['SWAP_ENTRIES']]
        chosen_function = np.random.choice(['AFFINE','LINEAR','BITPERM','SWAP_ENTRIES'], p = probabilities)
        
        if chosen_function == 'AFFINE':
            A = sbox_functions.get_random_linear_permutation()
            B = sbox_functions.get_random_linear_permutation()
            a = np.random.randint(0,16)
            b = np.random.randint(0,16)
            return sbox_functions.affine_transformation_front(B,b,sbox_functions.affine_transformation_back(A,a,sbox))
        elif chosen_function == 'LINEAR':
            A = sbox_functions.get_random_linear_permutation()
            B = sbox_functions.get_random_linear_permutation()
            return sbox_functions.matrix_transformation_front(B,sbox_functions.matrix_transformation_back(A,sbox))
        elif chosen_function == 'BITPERM':
            A = sbox_functions.get_random_bit_permutation()
            B = sbox_functions.get_random_bit_permutation()
            return sbox_functions.matrix_transformation_front(B,sbox_functions.matrix_transformation_back(A,sbox))
        elif chosen_function == 'SWAP_ENTRIES':
            while True:
                new_sbox = sbox_functions.swap_entries(sbox)
                ddt = sbox_functions.get_ddt(new_sbox)
                ddt[0,0] = 0
                if np.max(ddt) == 4:
                    return new_sbox
        return None

    ############ sbox initialization ############
    @staticmethod
    def get_good_sbox():
        while True:
            sbox_index = np.random.choice(len(sbox_functions.good_sboxes))
            sbox = sbox_functions.good_sboxes[sbox_index]
            sbox = sbox_functions.mutate(sbox,init=True)

            if sbox_functions.count_one_to_one_transitions(sbox) > 7: continue
            ddt = sbox_functions.get_ddt(sbox)
            ddt[0,0] = 0
            if np.max(ddt) > 4: continue
            # print(sbox_functions.count_one_to_one_transitions(sbox))
            # print('here')
            return sbox
    
    @staticmethod
    def get_mantis_sbox_w_mutation():
        sbox = [0xc,0xa,0xd,0x3,0xe,0xb,0xf,0x7,0x8,0x9,0x1,0x5,0x0,0x2,0x4,0x6]
        return sbox_functions.mutate(sbox,init=True)
    

class substitution_functions(sbox_functions):
    # for diversity computation
    @staticmethod
    def compute_distance(substitutionA,substitutionB):
        substitutionA = [tuple(substitutionA.sboxes[i]) for i in range(16)]
        substitutionB = [tuple(substitutionB.sboxes[i]) for i in range(16)]
        return 1 - len(set(substitutionA) & set(substitutionB)) / 16