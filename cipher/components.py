"""
This file contains
class trail: used to store the differential characteristics and linear trails
class linear_layer: contains a matrix and helper functions for the linear layer
class substitution_layer: contains a list of sboxes and helper functions for the substitution layer
class round_function: contains a list of layers (subst/linear) and helper functions
"""

from cipher.linear_functions import *
from cipher.sbox_functions import *
import re
import sys
import copy

from seed_config import SEED, set_global_seed
set_global_seed(SEED)

class trail:
    def __init__(self):
        self.before = [] # these are going to be store as list of list 
        self.after = [] # these are going to be store as list of list 
        self.probs = [] # storing the probability for each round
        self.start = None
        self.length = 0

    def add_trail(self,before,after,prob):
        self.before.append(before)
        self.after.append(after)
        self.probs.append(prob)
        self.length += 1

    def reset(self):
        self.after = []
        self.before = []
        self.probs = []
        self.start = None
        self.length = 0

    def read_trails_from_cnf(self,output_sat_file,vars_before_subst,vars_after_subst,probability_vars,nsbox=16,sbox_size=4):
        pattern_sat = re.compile(r's SATISFIABLE')

        # reading the file to parse the output
        with open(output_sat_file,'r') as f:
            line = f.readline().replace('\n','')
            m = re.match(pattern_sat,line)
            if m: flag = True
            else: flag = False
            
            res_dict = {}
            for line in f:
                if line[0] == 'v':
                    line = line[1:].split( )
                    vars = list(map(int,line))
                    for var in vars:
                        if var < 0: res_dict[-var] = 0
                        elif var > 0: res_dict [var] = 1
        if not flag: 
            raise Exception('Attempting to read the trail when solution is unsat!')

        nr = len(vars_before_subst)
        for n in range(nr):
            before = []
            for i in range(nsbox):
                for j in range(sbox_size):
                    before.append(res_dict[vars_before_subst[n][sbox_size*(15-i) + j]])
            prob = 0
            for i in range(nsbox):
                for j in range(len(probability_vars[n][i])):
                    prob += res_dict[probability_vars[n][i][j]]
            after = []
            for i in range(nsbox):
                for j in range(sbox_size):
                    after.append(res_dict[vars_after_subst[n][sbox_size*(15-i) + j]])   
            self.add_trail(before,after,prob)

    def print_trail(self,file=sys.stdout):
        print('start_from: %s' % (self.start),file=file)
        print('length: %s' % (self.length),file=file)
        for n in range(self.length):
            print('B Sbox:\t',end='',file=file)
            for i in range(len(self.before[n])):
                print(self.before[n][i],file=file,end='')
                if i % 4 == 3:print('\t',file=file,end='')
            print('\tprob: %s' % (self.probs[n]),file=file,end='\n')
            print('A Sbox:\t',end='',file=file)
            for i in range(len(self.after[n])):
                print(self.after[n][i],file=file,end='')
                if i % 4 == 3:print('\t',file=file,end='')
            print(file=file)
            print(file=file)
    
class linear_layer:
    def __init__(self):
        self.matrix = None
    def randomize(self):
        self.matrix = linear_functions.get_linear()

    def smart_randomize(self,diff_trail=None,linear_trail=None,front=False):
        if diff_trail == None and linear_trail == None: # move to the default if diff_trail and linear_trail are not given
            self.randomize()
            return
        
        # intialize a random one so we can compare it "do-while loop"
        self.randomize()
        if front:
            diff_vector = (linear_functions.inverse(self.matrix)).dot(diff_trail[0]) % 2
            linear_vector = (linear_functions.inverse(self.matrix.T)).dot(linear_trail[0]) % 2
        else:
            diff_vector = self.matrix.dot(diff_trail[-1]) % 2
            linear_vector = (self.matrix.T).dot(linear_trail[-1]) % 2

        diff_no_active_sbox = sum([1 if sbox_functions.bin2int(diff_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
        linear_no_active_sbox = sum([1 if sbox_functions.bin2int(linear_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
        best_count = min(diff_no_active_sbox,linear_no_active_sbox)
        best_matrices = [self.matrix]
        
        for _ in range(config.BRUTEFORCE['LINEAR_ATTEMPTS_PER_CIPHER']):
            self.randomize()
            if front:
                diff_vector = (linear_functions.inverse(self.matrix)).dot(diff_trail[0]) % 2
                linear_vector = (linear_functions.inverse(self.matrix.T)).dot(linear_trail[0]) % 2
            else:
                diff_vector = self.matrix.dot(diff_trail[-1]) % 2
                linear_vector = (self.matrix.T).dot(linear_trail[-1]) % 2
                
            diff_no_active_sbox = sum([1 if sbox_functions.bin2int(diff_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
            linear_no_active_sbox = sum([1 if sbox_functions.bin2int(linear_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
        
            count = min(diff_no_active_sbox,linear_no_active_sbox)
            if count > best_count: 
                best_matrices = [self.matrix]
                best_count = count
            elif count == best_count:
                best_matrices.append(self.matrix)
        idx = np.random.choice(len(best_matrices))
        self.matrix = best_matrices[idx].copy()

    def mutate(self):
        self.matrix = linear_functions.mutate(self.matrix)
    def is_equal(self,linear):
        return np.array_equal(self.matrix,linear.matrix)

class substitution_layer:
    def __init__(self):
        self.sboxes = []
        self.num_sboxes = 0

    def add_sbox(self,sbox):
        self.sboxes.append(sbox)
        self.num_sboxes += 1

    def randomize(self):
        if config.INIT_SETTINGS['SBOX']['MANTIS_ONLY']: # special consideration for mantis
            self.sboxes = [sbox_functions.get_mantis_sbox_w_mutation() for _ in range(16)]
        elif config.INIT_SETTINGS['SBOX']['MAX_4_DIFF_UNIFORMITY']:
            self.sboxes = [sbox_functions.get_good_sbox() for _ in range(16)]
        self.num_sboxes = len(self.sboxes)

    def mutate(self):
        index = np.random.randint(0,16)
        self.sboxes[index] = sbox_functions.mutate(self.sboxes[index])
    
    def is_equal(self,subst):
        for i in range(len(self.sboxes)):
            if self.sboxes[i] != subst.sboxes[i]: return False
        return True
    
class round_function:
    def __init__(self):
        self.round_index = None
        self.substitution = substitution_layer()
        self.linear = linear_layer()

    def randomize(self):
        self.substitution.randomize()
        self.linear.randomize()

    def steal_one_round(self,generation,cipher):
        # get the one with the best security latency ratio
        try:
            if cipher.num_rounds % 2 == 0: # add to the front
                diff_trail = cipher.diff_trails[0].before[0]
                linear_trail = cipher.linear_trails[0].before[0]
            else:
                diff_trail = cipher.diff_trails[-1].after[-1]
                linear_trail = cipher.linear_trails[-1].after[-1]
            best_count = 0
            best_matrices = []
            best_indices = []
            for member in generation:
                for rd_num,rd in enumerate(member.round_functions):
                    if rd.linear == None: continue
                    else:
                        matrix = rd.linear.matrix
                    if cipher.num_rounds % 2 == 0: # taking from the front
                        diff_vector = linear_functions.inverse(matrix).dot(diff_trail) % 2
                        linear_vector = linear_functions.inverse(matrix.T).dot(linear_trail) % 2
                    else:
                        diff_vector = matrix.dot(diff_trail) % 2
                        linear_vector = (matrix.T).dot(linear_trail) % 2
                    diff_no_active_sbox = sum([1 if sbox_functions.bin2int(diff_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
                    linear_no_active_sbox = sum([1 if sbox_functions.bin2int(linear_vector[4*i:4*i+4]) > 0 else 0 for i in range(16)])
                    count = min(diff_no_active_sbox,linear_no_active_sbox)
                    
                    if count > best_count: 
                        best_count = count
                        if cipher.num_rounds % 2 == 0: # taking from the front
                            best_indices = [(member.pop_index,rd_num)]
                            best_substitutions = [copy.deepcopy(member.round_functions[rd_num].substitution)]
                        else:
                            best_indices = [(member.pop_index,rd_num+1)]
                            best_substitutions = [copy.deepcopy(member.round_functions[rd_num+1].substitution)]
                        best_matrices = [matrix]
                    elif count == best_count:
                        if cipher.num_rounds % 2 == 0: # taking from the back
                            best_indices.append((member.pop_index,rd_num))
                            best_substitutions.append(copy.deepcopy(member.round_functions[rd_num].substitution))
                        else:
                            best_indices.append((member.pop_index,rd_num+1))
                            best_substitutions.append(copy.deepcopy(member.round_functions[rd_num+1].substitution))
                        best_matrices.append(matrix)
            choice = np.random.choice([i for i in range(len(best_matrices))])
            self.linear.matrix = best_matrices[choice]
            self.substitution = best_substitutions[choice]
        except Exception as e:
            print(e)
            assert False
            print('randomized')
            self.randomize()
            return None

    def smart_randomize(self,diff_trail=None,linear_trail=None,front=False):
        self.substitution.randomize()
        self.linear.smart_randomize(diff_trail=diff_trail,linear_trail=linear_trail,front=front)

    def add_substitution_layer(self,substitution):
        self.substitution = substitution
        
    def add_linear_layer(self,linear):
        self.linear = linear

    def mutate(self):
        if self.linear == None:
            self.substitution.mutate()
        else:
            if np.random.randint(0,2) == 0:
                self.substitution.mutate()
            else:
                self.linear.mutate()

    def is_equal(self,rf):
        if self.linear != None:
            return self.substitution.is_equal(rf.substitution) and self.linear.is_equal(rf.linear) 
        else:
            return self.substitution.is_equal(rf.substitution)