'''
This file has
class differential: static methods that are used to form the DIMACS file to compute the differential characteristic using SAT
class linear: static methods that are used to form the DIMACS file to compute the linear trail using SAT
class common: static methods that are used to form the DIMACS file for SAT
'''


import numpy as np
import os
import subprocess
import re
from itertools import chain
import utils
import config
from cipher.sbox_functions import *
        

class common:
    @staticmethod
    def generate_cnf_vars(probability,nr,state_size=64,max_weight=3,num_sboxes=16):
        # forming the variables
        variable_count = 1
        vars_before_subst = []
        vars_after_subst = []
        for _ in range(nr):
            vars_before_subst.append([i for i in range(variable_count,variable_count+state_size)])
            variable_count += state_size
        for _ in range(nr):
            vars_after_subst.append([i for i in range(variable_count,variable_count+state_size)])
            variable_count += state_size
        probability_vars = [[[] for _ in range(num_sboxes)] for _ in range(nr)]
        for _ in range(max_weight): # each '1' represents the weight. Max weight = 3
            for n in range(nr):
                for nsbox in range(num_sboxes):
                    probability_vars[n][nsbox].append(variable_count)
                    variable_count += 1
        # prepare for sequential encoding
        auxiliary_vars = [[0 for _ in range(probability)] for _ in range(max_weight * nr * num_sboxes)]
        for i in range(nr * max_weight * num_sboxes):
            for j in range(probability):
                auxiliary_vars[i][j] = variable_count
                variable_count += 1
        return vars_before_subst,vars_after_subst,auxiliary_vars,probability_vars,variable_count

    @staticmethod
    def compute_espresso(input_espresso_file,output_espresso_file,impossibilities,sbox_size=4,max_weight=3):
        with open(input_espresso_file,'w') as f:
            print('.i %s' % (2 * sbox_size + max_weight),file=f)
            print('.o %s' % (1),file=f)
            for i in range(len(impossibilities)):
                if impossibilities[i] == 0: continue
                line = str(sbox_functions.int2bin(i,2 * sbox_size + max_weight))
                line = line[1:len(line)-1].replace(',','').replace(' ','')
                print(line,file=f,end='')
                print(' 1',file=f)
            print('.e',file=f,end='')
        
        with open(output_espresso_file, "w") as out:
            subprocess.run(
                [config.FILE_PATHS['ESPRESSO'], input_espresso_file],
                stdout=out,
                check=True
            )
        # os.system('%s %s > %s' % (config.FILE_PATHS['ESPRESSO'], input_espresso_file, output_espresso_file))

    @staticmethod
    def parse_espresso_output(output_espresso_file,sbox_inputs,sbox_outputs,sbox_probs,sbox_size=4,max_weight=3):
        statements = []
        # parsing output from espresso
        
        impossible_lines = []
        with open(output_espresso_file,'r') as f:
            lines = f.readlines()
        for line in lines:
            if '.' == line[0]: continue
            if '.e' in line: break
            impossible_lines.append(line.split(' ')[0])
        for line in impossible_lines:
            try:
                s = ''
                for i in range(sbox_size): 
                    if line[i] == '0': s += str(sbox_inputs[i]) + ' '
                    elif line[i] == '1': s += str(-sbox_inputs[i]) + ' '
                for i in range(sbox_size):
                    if line[i+sbox_size] == '0': s += str(sbox_outputs[i]) + ' '
                    elif line[i+sbox_size] == '1': s += str(-sbox_outputs[i]) + ' '
                for i in range(max_weight):
                    if line[i+2*sbox_size] == '0': s += str(sbox_probs[i]) + ' '
                    elif line[i+2*sbox_size] == '1': s += str(-sbox_probs[i]) + ' '
                s += '0'
                statements.append(s)
            except:
                print(output_espresso_file, line)
                assert False

        return statements

    @staticmethod
    def encoding_xors(lst):
        # ensure that we have odd numbers of negatives all the time
        statements = []
        for i in range(1 << len(lst)):
            if bin(i).count('1') % 2 == 0: continue
            sign = bin(i)[2:].zfill(len(lst))
            s = ''
            for j in range(len(lst)):
                if sign[j] == '0': s += str(lst[j]) + ' '
                elif sign[j] == '1': s += str(-lst[j]) + ' '
            s += ' 0'
            statements.append(s)
        return statements
    
    @staticmethod
    def sequential_encoding(probability_vars,auxiliary_vars,probability):
        # aux_vars[i][j] --> the variable should be true if the first i^th vars have a sum of j
        # the variables should be less than probability
        statements = []
        
        probability_vars = list(chain.from_iterable(chain.from_iterable(probability_vars)))
        n = len(probability_vars)
        if probability > 0:
            statements.append(str(-probability_vars[0]) + ' ' + str(auxiliary_vars[0][0]) + ' 0') # S00 --> if up to the 0th index sums up to 1
            for j in range(1, probability): 
                statements.append(str(-auxiliary_vars[0][j]) + ' 0')
            for i in range(1, n-1):
                statements.append(str(-probability_vars[i]) + ' ' + str(auxiliary_vars[i][0]) + ' 0')
                statements.append(str(-auxiliary_vars[i-1][0]) + ' ' + str(auxiliary_vars[i][0]) + ' 0')
                statements.append(str(-probability_vars[i]) + ' ' + str(-auxiliary_vars[i-1][probability-1]) + ' 0') 
            for j in range(1, probability):
                for i in range(1, n-1):
                    statements.append(str(-probability_vars[i]) + ' ' + str(-auxiliary_vars[i-1][j-1]) + ' ' + str(auxiliary_vars[i][j]) + ' 0')
                    statements.append(str(-auxiliary_vars[i-1][j]) + ' ' + str(auxiliary_vars[i][j]) + ' 0')
            statements.append(str(-probability_vars[n-1]) + ' ' + str(-auxiliary_vars[n-2][probability-1]) + ' 0')
        if (probability == 0):
            for i in range(n):
                statements.append(str(-probability_vars[i]) + ' 0')
        return statements

    @staticmethod
    def run_sat_solver(input_sat_file,output_sat_file):
        # if config.SECURITY['MAX_TIME'] == None:
        #     cmd = '%s -q %s > %s' % (config.FILE_PATHS['SAT_SOLVER'],input_sat_file,output_sat_file)
        # else:
        #     cmd = 'timeout %s %s -q %s > %s' % (config.SECURITY['MAX_TIME'],config.FILE_PATHS['SAT_SOLVER'],input_sat_file,output_sat_file)

        
        # os.system(cmd)

        cmd = [
            config.FILE_PATHS['SAT_SOLVER'],
            "-q",
            input_sat_file
        ]
        try:
            with open(output_sat_file, "w") as out:
                if config.SECURITY['MAX_TIME'] is None:
                    subprocess.run(
                        cmd,
                        stdout=out,
                        stderr=subprocess.DEVNULL,
                        check=False
                    )
                else:
                    subprocess.run(
                        cmd,
                        stdout=out,
                        stderr=subprocess.DEVNULL,
                        timeout=config.SECURITY['MAX_TIME'],
                        check=False
                    )
        except subprocess.TimeoutExpired:
            # Timeout → treat as UNSAT (your original behavior)
            return "unsat"

        with open(output_sat_file,'r') as f: res = f.readlines()
        if 's SATISFIABLE\n' in res:
            return 'sat'
        elif 's UNSATISFIABLE\n' in res:
            return 'unsat'
        else:
            return 'unsat' # timeout = unsat
            # assert output_sat_file + ' does not contain SAT or UNSAT statement'
            

class differential:
    @staticmethod
    def compute_impossibles_from_DDT(DDT,max_diff_weight=3):
        sbox_size = int(np.log2(DDT.shape[0]))
        impossibilities = [1 for _ in range(1 << (sbox_size * 2 + max_diff_weight))]
        for i in range(1 << sbox_size):
            for j in range(1 << sbox_size):
                if DDT[i][j] > 0:
                    weight = -int(np.ceil(np.log2(DDT[i][j]/(1 << sbox_size))))
                    impossibilities[(i << (sbox_size + max_diff_weight)) + (j << max_diff_weight) + ((1 << weight) - 1)] = 0
        return impossibilities
    
    @staticmethod
    def get_subst_layer_cnf(layer,x,y,probability_vars,file_index,main_file,state_size=64,sbox_size=4,max_diff_weight=3):
        # using espresso to optimize the expression for sboxes
        input_espresso_file = './%s_tmp/espresso_diff_tmp/espresso_input_%s' % (main_file,file_index)
        # print('init_start',input_espresso_file)
        statements = []
        for sbox_index,sbox in enumerate(layer.sboxes):
            # print('start',input_espresso_file)
            sbox_string = ''.join([hex(sbox[i])[2:] for i in range(1 << sbox_size)])
            output_espresso_file = './%s_tmp/espresso_diff/espresso_output_%s.txt' % (main_file,sbox_string)
            if not os.path.exists(output_espresso_file): # check if we have computed it before
                DDT = sbox_functions.get_ddt(sbox)
                impossibilities = differential.compute_impossibles_from_DDT(DDT)
                output_espresso_file = './%s_tmp/espresso_diff_tmp/espresso_output_%s_%s.txt' % (main_file,sbox_string,file_index)
                # print('recomputing',output_espresso_file)
                common.compute_espresso(input_espresso_file,output_espresso_file,impossibilities,sbox_size,max_diff_weight)
            # preparing for parsing espresso output
            sbox_inputs = []
            sbox_outputs = []
            for i in range(sbox_size):
                sbox_inputs.append(x[state_size-sbox_size*sbox_index-(sbox_size-i)])
                sbox_outputs.append(y[state_size-sbox_size*sbox_index-(sbox_size-i)])
            sbox_probs = probability_vars[15-sbox_index]
            statements.extend(common.parse_espresso_output(\
                output_espresso_file,sbox_inputs,sbox_outputs,sbox_probs,sbox_size,max_diff_weight))
        return statements
    
    @staticmethod
    def get_linear_layer_cnf(layer,x,y):
        mat_size = layer.matrix.shape[0]
        statements = []
        for i in range(mat_size):
            indices = [63-j for j in range(mat_size) if layer.matrix[mat_size-i-1,mat_size-j-1] == 1]
            statements.extend(common.encoding_xors([y[63-i]] + [x[j] for j in indices]))
        return statements
    


class linear:

    @staticmethod
    def compute_impossibles_from_LAT(LAT,max_linear_weight=2):
        sbox_size = int(np.log2(LAT.shape[0]))
        impossibilities = [1 for _ in range(1 << (sbox_size * 2 + max_linear_weight))]
        for i in range(1 << sbox_size):
            for j in range(1 << sbox_size):
                if np.abs(LAT[i][j]) > 0:
                    weight = -int(np.ceil(np.log2(np.abs(LAT[i][j])/(1 << (sbox_size-1)))))
                    impossibilities[(i << (sbox_size + max_linear_weight)) + (j << max_linear_weight) + ((1 << weight) - 1)] = 0
        return impossibilities

    @staticmethod
    def get_subst_layer_cnf(layer,x,y,correlation_vars,file_index,main_file,state_size=64,sbox_size=4,max_linear_weight=2):
        # using espresso to optimize the expression for sboxes
        input_espresso_file = './%s_tmp/espresso_linear_tmp/espresso_input_%s' % (main_file,file_index)
        statements = []
        for sbox_index,sbox in enumerate(layer.sboxes):
            sbox_string = ''.join([hex(sbox[i])[2:] for i in range(1 << sbox_size)])
            output_espresso_file = './%s_tmp/espresso_linear/espresso_output_%s.txt' % (main_file,sbox_string)
            if not os.path.exists(output_espresso_file): # check if we have computed it before
                LAT = sbox_functions.get_lat(sbox)
                impossibilities = linear.compute_impossibles_from_LAT(LAT)
                output_espresso_file = './%s_tmp/espresso_linear_tmp/espresso_output_%s_%s.txt' % (main_file,sbox_string,file_index)
                common.compute_espresso(input_espresso_file,output_espresso_file,impossibilities,sbox_size,max_linear_weight)
            # preparing for parsing espresso output
            sbox_inputs = []
            sbox_outputs = []
            for i in range(sbox_size):
                sbox_inputs.append(x[state_size-sbox_size*sbox_index-(sbox_size-i)])
                sbox_outputs.append(y[state_size-sbox_size*sbox_index-(sbox_size-i)])
            sbox_probs = correlation_vars[15-sbox_index]
            statements.extend(common.parse_espresso_output(\
                output_espresso_file,sbox_inputs,sbox_outputs,sbox_probs,sbox_size,max_linear_weight))
        return statements

    @staticmethod
    def get_linear_layer_cnf(layer,x,y):
        mat_size = layer.matrix.shape[0]
        # check this again
        # a = L^T(x)
        # matrix = linear_functions.inverse(layer.matrix.T)
        statements = []
        for i in range(mat_size):
            indices = [j for j in range(mat_size) if layer.matrix[j,i] == 1]
            statements.extend(common.encoding_xors([x[i]] + [y[j] for j in indices]))
        return statements


