'''
class Member: Contains the data structure of a cipher, including properties useful in the genetic algorithm.
class Generation: Contains info about the current generation
'''

from cipher.linear_functions import *
from cipher.sbox_functions import *
import analysis.latency_computation as latency
import analysis.security_computation as security
import cipher.components as components

import utils
import numpy as np
import config
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor
import json
import warnings
try:
    from yosys.main import Yosys
except ImportError:  # pyosys is optional when the plugin evaluator is used
    Yosys = None
import os
from pathlib import Path

from seed_config import SEED, set_global_seed
set_global_seed(SEED)


def _as_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_number_list(value):
    if value is None:
        return []
    if isinstance(value, (int, float)):
        return [_as_float(value)]
    if not isinstance(value, (list, tuple)):
        return []
    return [_as_float(item) for item in value if isinstance(item, (int, float))]


def _safe_member_fingerprint(member):
    try:
        return member.candidate_fingerprint()
    except Exception as exc:
        return 'error:%s:%s' % (type(exc).__name__, exc)


def _stable_member_id(member):
    """Return the durable identifier used at plugin and crossover boundaries."""
    for attribute in ('candidate_id', 'identifier'):
        value = getattr(member, attribute, None)
        if value not in (None, ''):
            return str(value)
    generation = getattr(member, 'gen_index', None)
    population = getattr(member, 'pop_index', None)
    if generation is not None and population is not None:
        return 'g%s-p%s' % (generation, population)
    return None


def _reset_evaluation_state(member, status='pending', clear_mutation_changes=False):
    """Invalidate metrics after a structural change to a candidate."""
    for attribute in (
        'security_diff', 'diff_trails', 'security_linear', 'linear_trails',
        'latency', 'fitness', 'diversity', 'evaluation_error',
        'plugin_security', 'plugin_validation', 'plugin_performance',
    ):
        setattr(member, attribute, None)
    member.evaluation_status = status
    if clear_mutation_changes:
        member.mutation_changes = []

class Member:
    def __init__(self):
        self.num_rounds = 0
        self.security_diff = None
        self.diff_trails = None
        self.security_linear = None
        self.linear_trails = None
        self.latency = None
        self.fitness = None
        self.diversity = None
        self.round_functions = []
        self.pop_index = None
        self.gen_index = None
        self.identifier = None
        # Metadata is deliberately plain Python data so Member remains pickleable
        # when legacy fitness evaluation uses ProcessPoolExecutor.
        self.evaluation_status = None
        self.evaluation_error = None
        self.plugin_security = None
        self.plugin_validation = None
        self.plugin_performance = None
        self.crossover_strategy = None
        self.crossover_details = {}
        self.parent_ids = []
        self.mutation_changes = []
        self.candidate_id = None
        self.is_elite = False

    def __setstate__(self, state):
        """Load generations pickled before the framework metadata was added."""
        self.__dict__.update(state)
        defaults = {
            'evaluation_status': None,
            'evaluation_error': None,
            'plugin_security': None,
            'plugin_validation': None,
            'plugin_performance': None,
            'crossover_strategy': None,
            'crossover_details': {},
            'parent_ids': [],
            'mutation_changes': [],
            'candidate_id': None,
            'is_elite': False,
        }
        for key, value in defaults.items():
            if not hasattr(self, key):
                setattr(self, key, deepcopy(value))

    def to_candidate_dict(self):
        """Return the stable JSON contract consumed by B/C and the LLM planner."""
        from team_plugins.plugin_contracts import candidate_to_dict
        return candidate_to_dict(self, validate=True)

    def candidate_fingerprint(self):
        from team_plugins.plugin_contracts import candidate_fingerprint
        return candidate_fingerprint(self.to_candidate_dict())

    def randomize(self,nr=1):
        self.round_functions = []
        self.num_rounds = 0
        self.mutation_changes = []
        self.parent_ids = []
        self.crossover_strategy = None
        for n in range(nr-1):
            r = components.round_function()
            r.randomize()
            self.add_round_function(r)
        r = components.round_function()
        r.randomize()
        r.linear = None
        self.add_round_function(r)
        # self.print_member()

    def steal_one_round(self,generation):
        # member function that steal a round from someone in the generation
        r = components.round_function()
        r.steal_one_round(generation,self) # give the generation and what member (mainly to extract info from it)
        if self.num_rounds % 2 == 0: # add to the front
            self.round_functions.insert(0,r)
            for i,rf in enumerate(self.round_functions):
                rf.round_index = i
                rf.substitution.round_index = i
            self.num_rounds += 1
        else:
            self.round_functions[-1].linear = r.linear
            r.linear = None
            self.add_round_function(r)
        
    def smart_randomize_one_round(self):
        r = components.round_function()
        try:
            if self.num_rounds % 2 == 0: # add to the front
                diff_trail = self.diff_trails[0].before
                linear_trail = self.linear_trails[0].before
            else:
                diff_trail = self.diff_trails[-1].after
                linear_trail = self.linear_trails[-1].after
        except:
            diff_trail = None
            linear_trail = None

        r.smart_randomize(diff_trail=diff_trail,linear_trail=linear_trail,front=self.num_rounds % 2)

        if self.num_rounds % 2 == 0: # add to the front
            self.round_functions.insert(0,r)
            for i,rf in enumerate(self.round_functions):
                rf.round_index = i
                rf.substitution.round_index = i
            self.num_rounds += 1
        else: # add to the back
            self.round_functions[-1].linear = r.linear
            r.linear = None
            self.add_round_function(r)



    def add_round_function(self,round_function):
        round_function.round_index = self.num_rounds
        round_function.substitution.round_index = self.num_rounds
        self.round_functions.append(round_function)
        self.num_rounds += 1

    def mutate(self,prob):
        """Apply one child-level mutation event with probability ``prob``."""
        if not self.round_functions or np.random.uniform(0, 1) >= float(prob):
            return None
        round_index = int(np.random.randint(0, len(self.round_functions)))
        # Mutation is selected once per child.  The round function then
        # chooses whether this event changes its S-box layer or its complete
        # linear-layer component.
        mutation = self.round_functions[round_index].mutate()
        return {
            'round_index': round_index,
            'mutation': mutation,
        }
    
    def compute_fitness(self, context=None):
        """Evaluate this member using the configured plugin or legacy path."""
        mode = getattr(config, 'FRAMEWORK', {}).get('EVALUATION_MODE', 'legacy')
        if mode == 'plugins':
            return self.compute_plugin_fitness(context=context)
        return self.compute_legacy_fitness()

    def compute_legacy_fitness(self):
        if Yosys is None:
            raise RuntimeError('pyosys is required for legacy fitness evaluation')
        # docker_instance = openlane_containers.openlane_containers.get()
        # self.latency = self.compute_latency(docker_instance)
        self.latency = self.compute_latency()
        # openlane_containers.openlane_containers.put(docker_instance)
        window = min(self.num_rounds,config.SECURITY['MAX_WINDOW'])
        # print(window,self.num_rounds,self.pop_index)
        self.security_diff,self.diff_trails = self.compute_diff_security(window)
        self.security_linear,self.linear_trails = self.compute_linear_security(window)
        self.fitness = config.GENETIC_FUNCTIONS['FITNESS_FORMULA'][window](min(self.security_diff + [2*s for s in self.security_linear]),self.latency,self.num_rounds)
        self.evaluation_status = 'ok'
        self.evaluation_error = None
        return

    def compute_plugin_fitness(self, context=None):
        """Run the stable B/C interfaces without requiring their implementations."""
        from team_plugins.plugin_loader import (
            evaluate_security,
            validate_candidate,
            evaluate_performance,
        )

        base_context = dict(context or {})
        base_context.update({
            'generation': self.gen_index,
            'population_index': self.pop_index,
            'member_identifier': self.identifier,
        })
        try:
            candidate = self.to_candidate_dict()
            security_result = evaluate_security(candidate, base_context)
            validation_result = validate_candidate(candidate, base_context)
            performance_result = evaluate_performance(candidate, base_context)
            self.plugin_security = security_result
            self.plugin_validation = validation_result
            self.plugin_performance = performance_result

            status_values = [
                str(result.get('status', 'ok')).lower()
                for result in (security_result, validation_result, performance_result)
                if isinstance(result, dict)
            ]
            unavailable = any(status in {'unavailable', 'stub', 'disabled'} for status in status_values)
            invalid = any(status in {'invalid', 'error', 'failed'} for status in status_values)

            # A plugin may return ``status=ok`` while still marking a result
            # as unusable (for example Team C can set ``valid=False`` after a
            # failed engineering check).  Non-neutral fitness is allowed only
            # when all three plugin results explicitly accept the candidate.
            security_ok = (
                isinstance(security_result, dict)
                and bool(security_result.get('ok', security_result.get('status') == 'ok'))
            )
            validation_ok = (
                isinstance(validation_result, dict)
                and bool(validation_result.get('valid', False))
            )
            performance_ok = (
                isinstance(performance_result, dict)
                and bool(performance_result.get('valid', False))
            )
            plugin_accepts_candidate = security_ok and validation_ok and performance_ok

            differential = security_result.get('differential', security_result.get('security_diff', [])) \
                if isinstance(security_result, dict) else []
            linear = security_result.get('linear', security_result.get('security_linear', [])) \
                if isinstance(security_result, dict) else []
            if isinstance(differential, dict):
                differential = differential.get('weights', differential.get('values', []))
            if isinstance(linear, dict):
                linear = linear.get('weights', linear.get('values', []))
            self.security_diff = _as_number_list(differential)
            self.security_linear = _as_number_list(linear)
            performance_metrics = performance_result.get('metrics', {}) \
                if isinstance(performance_result, dict) else {}
            if not isinstance(performance_metrics, dict):
                performance_metrics = {}
            self.latency = _as_float(
                performance_result.get(
                    'latency',
                    performance_result.get(
                        'latency_ns', performance_metrics.get('latency', 0.0)
                    ),
                )
                if isinstance(performance_result, dict) else 0.0,
                default=0.0,
            )

            if unavailable or invalid or not plugin_accepts_candidate:
                self.fitness = 0.0
                self.evaluation_status = 'unavailable' if unavailable else 'invalid'
            else:
                sec_values = self.security_diff + [2 * value for value in self.security_linear]
                security_score = min(sec_values) if sec_values else _as_float(
                    security_result.get('score', 0.0), default=0.0
                )
                latency = max(self.latency, 1.0)
                window = min(self.num_rounds, len(config.GENETIC_FUNCTIONS['FITNESS_FORMULA']) - 1)
                formula = config.GENETIC_FUNCTIONS['FITNESS_FORMULA'][window]
                self.fitness = float(formula(security_score, latency, self.num_rounds))
                self.evaluation_status = 'ok'
            self.evaluation_error = None
        except Exception as exc:
            self.security_diff = []
            self.security_linear = []
            self.latency = 0.0
            self.fitness = 0.0
            self.evaluation_status = 'error'
            self.evaluation_error = '%s: %s' % (type(exc).__name__, exc)
        return self.fitness
    
    # Generating the cnf formula given the necessary details
    def _generate_diff_cnf(self,vars_before_subst,vars_after_subst,probability_vars,auxiliary_vars,probability,window_length,start_of_window,cnf=None):
        statements = []
        if cnf == None:
            # avoid trivial case
            s = ''
            for var in vars_before_subst[0]:
                s += '%s ' % (var)
            s += '0'
            statements.append(s)
            # round function
            for n_index,n in enumerate(range(start_of_window,start_of_window + window_length)):
                sec_statements = security.differential.get_subst_layer_cnf(\
                    self.round_functions[n].substitution,vars_before_subst[n_index],vars_after_subst[n_index],\
                    probability_vars[n_index],self.pop_index,config.FILE_PATHS['MAIN_FILE'])
                statements.extend(sec_statements)
                if n == (start_of_window + window_length - 1): continue # we ignore the last linear layer
                lin_statements = security.differential.get_linear_layer_cnf(\
                    self.round_functions[n].linear,vars_after_subst[n_index],vars_before_subst[n_index+1])
                statements.extend(lin_statements)
            cnf = deepcopy(statements) # to save some computation time
        else:
            statements = deepcopy(cnf)
        # forming the probabilities
        prob_statements = security.common.sequential_encoding(probability_vars,auxiliary_vars,probability)
        statements.extend(prob_statements)
        return statements,cnf
    
    # Computing a single instance of SAT
    def _compute_diff_cnf_using_sat(self,probability,window_length,start_of_window,cnf=None):
        input_sat_file = os.path.join(config.FILE_PATHS['SAT_DIFF_FOLDER'], 'sec_%s_input.cnf' % self.pop_index)
        output_sat_file = os.path.join(config.FILE_PATHS['SAT_DIFF_FOLDER'], 'sec_%s_output.cnf' % self.pop_index)
        vars_before_subst,vars_after_subst,auxiliary_vars,probability_vars,variable_count = security.common.generate_cnf_vars(probability,window_length)
        statements,cnf = self._generate_diff_cnf(vars_before_subst,vars_after_subst,probability_vars,auxiliary_vars,probability,window_length,start_of_window,cnf=cnf)
        statements.insert(0,'p cnf %s %s' % (variable_count-1,len(statements)))

        utils.write_to_file(input_sat_file,statements)
        sat_bool = security.common.run_sat_solver(input_sat_file,output_sat_file)
        if sat_bool == 'sat':
            self.diff_trails[start_of_window].reset()
            self.diff_trails[start_of_window].start = start_of_window
            self.diff_trails[start_of_window].read_trails_from_cnf(output_sat_file,vars_before_subst,vars_after_subst,probability_vars)
        return sat_bool,cnf

    # Given a window, computing the best differential characteristic
    def _compute_single_diff_security(self,init_probability,window_length,start_of_window=0):
        probability_bounds = [0,init_probability]
        # compute the upper bound
        cnf = None
        while True:
            sat_bool, cnf = self._compute_diff_cnf_using_sat(probability_bounds[1],window_length,start_of_window,cnf)
            if sat_bool == 'sat': 
                # print('upper bound is %s' % (probability_bounds))
                break # we found the upper bound
            elif sat_bool == 'unsat':
                probability_bounds[0] = probability_bounds[1]
                probability_bounds[1] = min(config.SECURITY['MAX_DIFF_SECURITY'], probability_bounds[1] + 2) # increment of 2 each time
            if probability_bounds[0] == probability_bounds[1] == config.SECURITY['MAX_DIFF_SECURITY']: # exceeded what we limited
                return config.SECURITY['MAX_DIFF_SECURITY']
        # compute the lower bound
        while True:
            if probability_bounds[0] >= probability_bounds[1] - 1: # terminating criteria
                return probability_bounds[1]
            else:
                probability = max(probability_bounds[0]+1,probability_bounds[1]-1)
                sat_bool, cnf = self._compute_diff_cnf_using_sat(probability,window_length,start_of_window,cnf)
                if sat_bool == 'sat': probability_bounds[1] = probability
                else: probability_bounds[0] = probability
                # print('lower bound is %s' % (probability_bounds))

    # Compute a list of best differential characteristics (windows)
    def compute_diff_security(self,window_length,start=0,end=999):
        if window_length >= self.num_rounds:
            self.diff_trails = [components.trail()]
            self.security_diff = [self._compute_single_diff_security(config.SECURITY['INIT_DIFF_SECURITY'][window_length],window_length,0)]
        else: 
            self.security_diff = []
            self.diff_trails = [components.trail() for _ in range(start,min(end,self.num_rounds+1-window_length))]
            for start_of_window in range(start,min(end,self.num_rounds+1-window_length)):
                self.security_diff.append(self._compute_single_diff_security(config.SECURITY['INIT_DIFF_SECURITY'][window_length],window_length,start_of_window))
        return self.security_diff,self.diff_trails
    
    # Generating the cnf formula given the necessary details
    def _generate_linear_cnf(self,vars_before_subst,vars_after_subst,correlation_vars,auxiliary_vars,correlation,start_of_window,window_length,cnf=None):
        statements = []
        if cnf == None:
            # avoid trivial case
            s = ''
            for var in vars_before_subst[0]:
                s += '%s ' % (var)
            s += '0'
            statements.append(s)
            # round function
            for n_index,n in enumerate(range(start_of_window,start_of_window + window_length)):
                sec_statements = security.linear.get_subst_layer_cnf(\
                    self.round_functions[n].substitution,vars_before_subst[n_index],vars_after_subst[n_index],\
                    correlation_vars[n_index],self.pop_index,config.FILE_PATHS['MAIN_FILE'])
                statements.extend(sec_statements)
                if n == (start_of_window + window_length - 1): continue # we ignore the last linear layer
                lin_statements = security.linear.get_linear_layer_cnf(\
                    self.round_functions[n].linear,vars_after_subst[n_index],vars_before_subst[n_index+1])
                statements.extend(lin_statements)
            cnf = deepcopy(statements) # to save some computation time
        else:
            statements = deepcopy(cnf)
        # forming the probabilities
        prob_statements = security.common.sequential_encoding(correlation_vars,auxiliary_vars,correlation)
        statements.extend(prob_statements)
        return statements,cnf

    # Computing a single instance of SAT
    def _compute_linear_cnf_using_sat(self,correlation,start_of_window,window_length,cnf=None):
        input_sat_file = os.path.join(config.FILE_PATHS['SAT_LINEAR_FOLDER'], 'sec_%s_input.cnf' % self.pop_index)
        output_sat_file = os.path.join(config.FILE_PATHS['SAT_LINEAR_FOLDER'], 'sec_%s_output.cnf' % self.pop_index)
        vars_before_subst,vars_after_subst,auxiliary_vars,correlation_vars,variable_count = security.common.generate_cnf_vars(correlation,window_length)

        statements,cnf = self._generate_linear_cnf(vars_before_subst,vars_after_subst,correlation_vars,auxiliary_vars,correlation,start_of_window,window_length,cnf=cnf)
        statements.insert(0,'p cnf %s %s' % (variable_count-1,len(statements)))

        utils.write_to_file(input_sat_file,statements)
        sat_bool = security.common.run_sat_solver(input_sat_file,output_sat_file)
        if sat_bool == 'sat':
            self.linear_trails[start_of_window].reset()
            self.linear_trails[start_of_window].start = start_of_window
            self.linear_trails[start_of_window].read_trails_from_cnf(output_sat_file,vars_before_subst,vars_after_subst,correlation_vars)
        return sat_bool,cnf

    # Given a window, computing the best linear trail
    def _compute_single_linear_security(self,init_correlation,window_length,start_of_window=0):
        correlation_bounds = [0,init_correlation]
        # compute the upper bound
        cnf = None
        while True:
            sat_bool, cnf = self._compute_linear_cnf_using_sat(correlation_bounds[1],start_of_window,window_length,cnf)
            if sat_bool == 'sat': break # we found the upper bound
            elif sat_bool == 'unsat':
                correlation_bounds[0] = correlation_bounds[1]
                correlation_bounds[1] = min(config.SECURITY['MAX_LINEAR_SECURITY'], correlation_bounds[1] + 2) # increment of 2 each time
            if correlation_bounds[0] == correlation_bounds[1] == config.SECURITY['MAX_LINEAR_SECURITY']: # exceeded what we limited
                return config.SECURITY['MAX_LINEAR_SECURITY']
        # compute the lower bound
        while True:
            if correlation_bounds[0] >= correlation_bounds[1] - 1: # terminating criteria
                return correlation_bounds[1]
            else:
                correlation = max(correlation_bounds[0]+1,correlation_bounds[1]-1)
                sat_bool, cnf = self._compute_linear_cnf_using_sat(correlation,start_of_window,window_length)
                if sat_bool == 'sat': correlation_bounds[1] = correlation
                else: correlation_bounds[0] = correlation
    
    # Compute a list of best linear trails (windows)
    def compute_linear_security(self,window_length,start=0,end=999):
        if window_length >= self.num_rounds:
            self.linear_trails = [components.trail()]
            self.security_linear = [self._compute_single_linear_security(config.SECURITY['INIT_LINEAR_SECURITY'][window_length],window_length,0)]
        else: 
            self.linear_trails = [components.trail() for _ in range(start,min(end,self.num_rounds+1-window_length))]
            self.security_linear = []
            for start_of_window in range(start,min(end,self.num_rounds+1-window_length)):
                self.security_linear.append(self._compute_single_linear_security(config.SECURITY['INIT_LINEAR_SECURITY'][window_length],window_length,start_of_window))
        return self.security_linear,self.linear_trails

    def _prepare_verilog_statements(self,verilog_file):
        statements = []
        statement = latency.prepare_preamble(self.num_rounds,verilog_file)
        statements.extend(statement)

        # Settling the key schedule and constants
        for n in range(self.num_rounds+1): 
            statement = latency.get_key_schedule_and_const(mkey_index=n % 2, rkey_index=n, constant_index=n); 
            statements.append(statement)

        # XOR the keys
        statements.append('\tassign t[%s] = x ^ kn[%s];' % (0,0));
        for n in range(1,self.num_rounds):
            statement = latency.get_add_key(t_index=3*n-1, rkey_index=n)
            statements.append(statement)
        statements.append('\tassign t[%i] = t[%i] ^ kn[%i];' % (3*n+2,3*n+1,self.num_rounds))
        statements.append('\tassign y = t[%i];' % (3*n+2))

        # subst layers
        for n in range(self.num_rounds):
            statement = latency.get_subst_layer(3*n,2*n); 
            statements.append(statement)

        # linear layers
        for n in range(1,self.num_rounds):
            statement = latency.get_linear_layer(3*n-2,2*n-1)
            statements.append(statement)

        statements.append('\tendmodule\n')

        # substLayer calling the sboxes
        for n in range(self.num_rounds):
            statement = latency.get_sboxes_in_subst_layer(2*n)
            statements.append(statement)

        # sboxes implementation
        for n in range(self.num_rounds):
            for i in range(16):
                statement = latency.get_sboxes_implementation(self.round_functions[n].substitution.sboxes[i],2*n,i)
                statements.append(statement)

        # linear implementation
        for n in range(self.num_rounds-1):
            statement = latency.get_matrix_implementation(self.round_functions[n].linear.matrix,2*n+1)
            statements.append(statement)

        # key add const implementation
        statement = latency.get_key_add_and_const_implementation()
        statements.append(statement)
        return statements

    def get_prince_full(self,nr):
        prince_sbox = [0xB,0xF,0x3,0x2,0xA,0xC,0x9,0x1,0x6,0x7,0x8,0x0,0xE,0x5,0xD,0x4]
        prince_m1 = linear_functions.get_prince_m1()
        prince_m2 = linear_functions.get_prince_m2()
        zero_matrix = np.zeros((16,16),dtype=int)
        prince_mc = linear_functions.get_aes_shiftrows().dot(np.block([
                                  [prince_m1,zero_matrix,zero_matrix,zero_matrix],
                                  [zero_matrix,prince_m2,zero_matrix,zero_matrix],
                                  [zero_matrix,zero_matrix,prince_m2,zero_matrix],
                                  [zero_matrix,zero_matrix,zero_matrix,prince_m1],]))
        prince_matrix = linear_functions.get_aes_shiftrows().dot(prince_mc)
        prince_matrix_inverse = linear_functions.inverse(prince_matrix)
        prince_inv_sbox = [-1 for _ in range(16)]
        for i in range(16): prince_inv_sbox[prince_sbox[i]] = i

        if nr % 2 == 0: 
            front = (nr-2) // 2
        else: 
            front = (nr-2) // 2 + 1
        back = nr - front - 2
        for _ in range(front):
            r = components.round_function()
            r_subst = components.substitution_layer()
            for _ in range(16): r_subst.add_sbox(prince_sbox)
            r.add_substitution_layer(r_subst)
            r_linear = components.linear_layer()
            r_linear.matrix = prince_matrix
            r.add_linear_layer(r_linear)
            self.add_round_function(r)

        # middle layer
        r = components.round_function()
        r_subst = components.substitution_layer()
        for _ in range(16): r_subst.add_sbox(prince_sbox)
        r.add_substitution_layer(r_subst)
        r_linear = components.linear_layer()
        r_linear.matrix = prince_mc
        r.add_linear_layer(r_linear)
        self.add_round_function(r)

        r = components.round_function()
        r_subst = components.substitution_layer()
        for _ in range(16): r_subst.add_sbox(prince_inv_sbox)
        r.add_substitution_layer(r_subst)

        for _ in range(back):
            r_linear = components.linear_layer()
            r_linear.matrix = prince_mc
            r.add_linear_layer(r_linear)
            self.add_round_function(r)
            r = components.round_function()
            r_subst = components.substitution_layer()
            for _ in range(16): r_subst.add_sbox(prince_inv_sbox)
            r.add_substitution_layer(r_subst)
        r.linear = None
        self.add_round_function(r)
        return

    def get_uknitbc(self,nr,window=0):
        project_root = Path(__file__).resolve().parents[1]
        configured = str(
            config.INIT_SETTINGS.get('UKNIT_BASELINE_PATH', '') or ''
        ).strip()
        candidates = []
        if configured:
            configured_path = Path(configured)
            candidates.append(
                configured_path
                if configured_path.is_absolute()
                else project_root / configured_path
            )
        candidates.extend([
            project_root / 'uknit64_cipher.pkl',
            project_root.parent / 'uknit64_cipher.pkl',
        ])
        # Keep the search order stable while removing duplicate paths.
        candidates = list(dict.fromkeys(path.resolve() for path in candidates))
        file = next((path for path in candidates if path.is_file()), None)
        if file is None:
            if config.INIT_SETTINGS.get('UKNIT_FALLBACK_RANDOM', True):
                # A missing baseline must not make the framework unusable.  A
                # random candidate has the same valid round shape, but it does
                # not represent a particular published uKNIT window.
                warnings.warn(
                    'uknit64_cipher.pkl was not found; using a random candidate '
                    'instead of the published uKNIT-BC baseline. The window '
                    'index is retained only as metadata.',
                    RuntimeWarning,
                    stacklevel=2,
                )
                self.randomize(nr)
                self.uknit_source = 'random_fallback'
                self.uknit_window = int(window)
                return
            searched = ', '.join(str(path) for path in candidates)
            raise FileNotFoundError(
                'INCLUDE_UKNIT=True requires the precomputed uKNIT-BC baseline '
                'uknit64_cipher.pkl. Searched: %s. Put the file in the project '
                'root, set UKNIT_BASELINE_PATH, or enable '
                'UKNIT_FALLBACK_RANDOM.' % searched
            )

        cipher = utils.pickle_load(file)
        source_rounds = getattr(cipher, 'round_functions', None)
        required_rounds = int(window) + int(nr)
        try:
            source_round_count = len(source_rounds)
        except TypeError:
            source_round_count = 0
        if source_round_count < required_rounds:
            raise ValueError(
                'uKNIT baseline %s has %s rounds, but window=%s and nr=%s '
                'require at least %s rounds.'
                % (file, source_round_count, window, nr, required_rounds)
            )

        self.round_functions = []
        self.num_rounds = 0
        for n in range(window,nr+window-1):
            self.add_round_function(deepcopy(source_rounds[n]))
        rf = deepcopy(source_rounds[nr+window-1])
        rf.linear = None
        self.add_round_function(rf)
        self.uknit_source = str(file)
        self.uknit_window = int(window)

    def get_prince(self,nr):
        # this only implements the front of prince
        prince_sbox = [0xB,0xF,0x3,0x2,0xA,0xC,0x9,0x1,0x6,0x7,0x8,0x0,0xE,0x5,0xD,0x4]
        prince_m1 = linear_functions.get_prince_m1()
        prince_m2 = linear_functions.get_prince_m2()
        zero_matrix = np.zeros((16,16),dtype=int)
        prince_matrix = linear_functions.get_aes_shiftrows().dot(np.block([
                                  [prince_m1,zero_matrix,zero_matrix,zero_matrix],
                                  [zero_matrix,prince_m2,zero_matrix,zero_matrix],
                                  [zero_matrix,zero_matrix,prince_m2,zero_matrix],
                                  [zero_matrix,zero_matrix,zero_matrix,prince_m1],]))
        self.round_functions = []
        self.num_rounds = 0
        for n in range(nr-1):
            r = components.round_function()
            r_subst = components.substitution_layer()
            for _ in range(16): r_subst.add_sbox(prince_sbox)
            r.add_substitution_layer(r_subst)
            r_linear = components.linear_layer()
            r_linear.matrix = prince_matrix
            r.add_linear_layer(r_linear)
            self.add_round_function(r)
        r = components.round_function()
        r_subst = components.substitution_layer()
        for _ in range(16): r_subst.add_sbox(prince_sbox)
        r.add_substitution_layer(r_subst)
        r.linear = None
        self.add_round_function(r)


    def compute_latency(self):
        if Yosys is None:
            raise RuntimeError('pyosys is required for latency evaluation')
        verilog_file = 'design_%s_%s.v' % (config.FILE_PATHS['MAIN_FILE'],self.pop_index)
        statements = self._prepare_verilog_statements(verilog_file)
        verilog_path = os.path.join(config.FILE_PATHS['VERILOG_FOLDER'], verilog_file)
        utils.write_to_file(verilog_path,statements)
        user_config = {
        "VERILOG_FILES": [verilog_path], # verilog file
        "DESIGN_NAME": "design_main_%s" % (self.pop_index), # the top module in the verilog files
        "ABC_OUTPUT_FILE" : "abc_%s.log" % (self.pop_index),
        "YOSYS_OUTPUT_FILE" : "yosys_%s.log" % (self.pop_index),
        "SAVE_NETLIST" : "design_main_%s.nl.v" % (self.pop_index), # file containing the final netlist
        }
        yosys_directory = str(Path(__file__).resolve().parents[1] / 'yosys') + os.sep
        yosys = Yosys(user_config,self.pop_index,directory=yosys_directory)
        yosys.run_yosys()
        return yosys.latency



    def is_equal(self,member):
        for i in range(self.num_rounds):
            if not self.round_functions[i].is_equal(member.round_functions[i]):
                return False
        return True
 
    def breed(self,member):
        if len(member.round_functions) != len(self.round_functions):
            raise Exception('Unable to breed 2 members with different round functions!')
        num_rounds = len(self.round_functions)
        parent_a_id = _stable_member_id(self) or 'parent-a'
        parent_b_id = _stable_member_id(member) or 'parent-b'
        parent_ids = [parent_a_id, parent_b_id]

        # There are three crossover strategies.  Build an explicit source map
        # first, then materialize both children from that map.  Keeping the map
        # makes the audit metadata describe the exact component provenance.
        choice = str(np.random.choice(
            list(config.GENETIC_ALGO['CROSSOVER'].keys()),
            p=list(config.GENETIC_ALGO['CROSSOVER'].values()),
        ))
        # The chromosome is [S0, L0, S1, L1, ..., S_(r-1)].  The final
        # substitution layer has no following linear component, so the final
        # ``None`` placeholder is not a crossover slot.
        component_count = max(1, 2 * num_rounds - 1)
        source_map_a = []
        source_map_b = []
        details = {
            'strategy': choice,
            'round_count': num_rounds,
            'parent_ids': list(parent_ids),
        }

        if choice == 'SINGLE':
            # A one-round chromosome contains only S_(r-1), so use a
            # degenerate cut record while preserving the valid child shape.
            cut = 1 if component_count <= 1 else int(
                np.random.randint(1, component_count)
            )
            cuts = [cut]
            details['cuts'] = list(cuts)
            details['cut_points'] = list(cuts)
            for component_index in range(component_count):
                first_half = component_index < cut
                source_map_a.append('parent_a' if first_half else 'parent_b')
                source_map_b.append('parent_b' if first_half else 'parent_a')
        elif choice == 'DOUBLE':
            if component_count <= 3:
                # Fewer than four chromosome components cannot provide two
                # distinct internal cuts.  Keep the requested strategy in the
                # audit record but use a single-cut degenerate form.
                cuts = [1]
                details['fallback'] = 'single_cut_insufficient_components'
            else:
                cuts = np.asarray(
                    np.random.choice(
                        range(1, component_count - 1), size=2, replace=False
                    ),
                    dtype=int,
                )
                cuts.sort()
                cuts = [int(value) for value in cuts]
            details['cuts'] = list(cuts)
            details['cut_points'] = list(cuts)
            for component_index in range(component_count):
                if len(cuts) == 1:
                    segment = 0 if component_index < cuts[0] else 1
                else:
                    segment = 0 if component_index < cuts[0] else (
                        1 if component_index < cuts[1] else 2
                    )
                source_map_a.append('parent_a' if segment % 2 == 0 else 'parent_b')
                source_map_b.append('parent_b' if segment % 2 == 0 else 'parent_a')
        elif choice == 'UNIFORM':
            # Uniform crossover is per S-box for substitution and per matrix
            # for the linear layer.  The final round has no linear component.
            round_sources = []
            for round_index in range(num_rounds):
                sbox_sources = []
                for _ in range(16):
                    source = 'parent_a' if int(np.random.randint(0, 2)) == 0 else 'parent_b'
                    sbox_sources.append(source)
                linear_source = None if round_index == num_rounds - 1 else (
                    'parent_a' if int(np.random.randint(0, 2)) == 0 else 'parent_b'
                )
                opposite_sbox_sources = [
                    'parent_b' if source == 'parent_a' else 'parent_a'
                    for source in sbox_sources
                ]
                opposite_linear_source = None if linear_source is None else (
                    'parent_b' if linear_source == 'parent_a' else 'parent_a'
                )
                round_sources.append({
                    'round_index': round_index,
                    'sbox_sources': sbox_sources,
                    'linear_source': linear_source,
                    'sbox_sources_child_b': opposite_sbox_sources,
                    'linear_source_child_b': opposite_linear_source,
                })
            details['rounds'] = round_sources
            details['round_sources'] = deepcopy(round_sources)
        else:
            raise ValueError('Unknown crossover strategy: %s' % choice)

        def _source_member(source_name):
            return self if source_name == 'parent_a' else member

        def _build_child(child_index):
            child = Member()
            for round_index in range(num_rounds):
                if choice == 'UNIFORM':
                    round_source = details['rounds'][round_index]
                    source_substitution = components.substitution_layer()
                    sbox_sources = (
                        round_source['sbox_sources']
                        if child_index == 0
                        else round_source['sbox_sources_child_b']
                    )
                    for sbox_index, source_name in enumerate(sbox_sources):
                        source_round = _source_member(source_name).round_functions[round_index]
                        source_substitution_layer = source_round.substitution
                        source_substitution.add_sbox(
                            deepcopy(source_substitution_layer.sboxes[sbox_index]),
                            input_permutation=deepcopy(
                                getattr(source_substitution_layer, 'input_permutations', [None] * 16)[sbox_index]
                            ),
                            output_permutation=deepcopy(
                                getattr(source_substitution_layer, 'output_permutations', [None] * 16)[sbox_index]
                            ),
                        )
                    linear_source_name = (
                        round_source['linear_source']
                        if child_index == 0
                        else round_source['linear_source_child_b']
                    )
                    if linear_source_name is None:
                        linear = None
                    else:
                        linear_source = _source_member(linear_source_name).round_functions[round_index]
                        linear = deepcopy(linear_source.linear)
                    substitution = source_substitution
                else:
                    source_name = (source_map_a if child_index == 0 else source_map_b)[2 * round_index]
                    source_round = _source_member(source_name).round_functions[round_index]
                    substitution = deepcopy(source_round.substitution)
                    if round_index == num_rounds - 1:
                        linear = None
                    else:
                        linear_name = (source_map_a if child_index == 0 else source_map_b)[2 * round_index + 1]
                        linear_round = _source_member(linear_name).round_functions[round_index]
                        linear = deepcopy(linear_round.linear)

                child_round = components.round_function()
                child_round.add_substitution_layer(substitution)
                child_round.add_linear_layer(linear)
                child.add_round_function(child_round)

            # Preserve the cipher invariant even when a caller supplies a
            # legacy/malformed parent whose final round still carries a linear
            # object (older pickles and small test fixtures can do this).
            if child.round_functions:
                child.round_functions[-1].linear = None
            self._validate_crossover_linear_layers(child)

            child.crossover_strategy = choice
            child.crossover_details = deepcopy(details)
            if choice in {'SINGLE', 'DOUBLE'}:
                source_entries = []
                source_map = source_map_a if child_index == 0 else source_map_b
                for component_index, source_name in enumerate(source_map):
                    source_entries.append({
                        'component_index': component_index,
                        'round_index': component_index // 2,
                        'component': 'sbox' if component_index % 2 == 0 else 'linear',
                        'source': source_name,
                        'source_id': parent_a_id if source_name == 'parent_a' else parent_b_id,
                    })
                child.crossover_details['component_sources'] = source_entries
            child.parent_ids = list(parent_ids)
            child.gen_index = self.gen_index
            _reset_evaluation_state(child, status='pending', clear_mutation_changes=True)
            return child

        return _build_child(0), _build_child(1)

    @staticmethod
    def _validate_crossover_linear_layers(child):
        """Validate complete linear components after crossover materialization."""
        rounds = getattr(child, 'round_functions', [])
        for round_index, round_function in enumerate(rounds):
            linear = getattr(round_function, 'linear', None)
            if round_index == len(rounds) - 1:
                if linear is not None:
                    raise ValueError('final round must not contain a linear layer')
                continue
            matrix = getattr(linear, 'matrix', None) if linear is not None else None
            if matrix is None or not linear_functions.is_valid_linear_matrix(
                matrix, row_column_weight=3
            ):
                raise ValueError(
                    'crossover produced an invalid 64x64 binary linear matrix '
                    f'at round {round_index}'
                )
    
    def print_member(self):
        print('num_rounds: %s' % (self.num_rounds))
        print('generation: %s' % (self.gen_index))
        for i in range(self.num_rounds-1):
            for j in range(16):
                print(self.round_functions[i].substitution.sboxes[j],end='')
                DDT = sbox_functions.get_ddt(self.round_functions[i].substitution.sboxes[j])
                DDT[0,0] = 0
                print(np.max(DDT),end='')
            for j in range(64):
                for k in range(64):
                    print(self.round_functions[i].linear.matrix[j][k],end='')
        for j in range(16):
            print(self.round_functions[self.num_rounds-1].substitution.sboxes[j],end='')
        print(self.round_functions[self.num_rounds-1].linear)

class Generation:
    def __init__(self,num_rounds,gen_index):
        self.num_rounds = num_rounds
        self.gen_index = gen_index
        self.num_member = 0
        self.members = []
        self.next_members = []
        self.fittest_population = []
        self.next_fittest_population = []
        self.breeding_population = []
        self.last_breeding_records = []
        self.last_mutation_report = {}
        self.last_round_growth_report = {}

    def __setstate__(self, state):
        self.__dict__.update(state)
        defaults = {
            'breeding_population': [],
            'last_breeding_records': [],
            'last_mutation_report': {},
            'last_round_growth_report': {},
        }
        for key, value in defaults.items():
            if not hasattr(self, key):
                setattr(self, key, deepcopy(value))
    
    def randomize(self,num):
        for _ in range(num):
            member = Member()
            member.randomize(self.num_rounds)
            member.gen_index = self.gen_index
            member.pop_index = self.num_member
            member.candidate_id = 'r%02d-g%04d-p%04d' % (
                self.num_rounds, self.gen_index, self.num_member
            )
            self.members.append(member)
            self.num_member += 1

    def add_member(self,member):
        member = deepcopy(member)
        member.gen_index = self.gen_index
        member.pop_index = self.num_member
        if not getattr(member, 'candidate_id', None):
            member.candidate_id = 'r%02d-g%04d-p%04d' % (
                self.num_rounds, self.gen_index, self.num_member
            )
        self.members.append(member)
        self.num_member += 1
        

    def select_fittest_population(self,num_fittest_population):
        pool = self.fittest_population + self.members
        self.next_fittest_population = sorted(
            pool, key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')), reverse=True
        )[:max(0, int(num_fittest_population))]
        # adjust back
        for index in range(len(self.next_fittest_population)):
            self.next_fittest_population[index] = deepcopy(self.next_fittest_population[index])
            self.next_fittest_population[index].is_elite = True

    def select_breeding_population(self,num_breeding_population):
        population = sorted(
            self.fittest_population + self.members,
            key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
            reverse=True,
        )
        target = min(max(0, int(num_breeding_population)), len(population))
        self.breeding_population = []
        remaining = list(population)
        fitness_weight, diversity_weight = config.GENETIC_ALGO['FITNESS_TO_DIVERSITY_RATIO'] \
            if 'FITNESS_TO_DIVERSITY_RATIO' in config.GENETIC_ALGO \
            else config.GENETIC_FUNCTIONS['FITNESS_TO_DIVERSITY_RATIO']
        while remaining and len(self.breeding_population) < target:
            raw_fitness = [_as_float(getattr(member, 'fitness', None), 0.0) for member in remaining]
            minimum = min(raw_fitness)
            shifted = [value - minimum for value in raw_fitness]
            total = sum(shifted)
            if total <= 0:
                normalized_fitness = [1.0 / len(remaining)] * len(remaining)
            else:
                normalized_fitness = [value / total for value in shifted]

            raw_diversity = []
            for member in remaining:
                if not self.breeding_population:
                    raw_diversity.append(1.0)
                else:
                    raw_diversity.append(sum(
                        self._compute_distance(member, selected)
                        for selected in self.breeding_population
                    ) / len(self.breeding_population))
            diversity_total = sum(max(0.0, value) for value in raw_diversity)
            if diversity_total <= 0:
                normalized_diversity = [1.0 / len(remaining)] * len(remaining)
            else:
                normalized_diversity = [max(0.0, value) / diversity_total for value in raw_diversity]
            scores = [
                float(fitness_weight) * fitness ** 2 + float(diversity_weight) * diversity ** 2
                for fitness, diversity in zip(normalized_fitness, normalized_diversity)
            ]
            selected_index = int(np.argmax(scores))
            selected = remaining.pop(selected_index)
            selected.diversity = raw_diversity[selected_index]
            self.breeding_population.append(selected)

    def _normalized_fitness(self,population):
        """Return normalized fitness without mutating the authoritative metric."""
        values = [_as_float(getattr(pop, 'fitness', None), 0.0) for pop in population]
        if not values:
            return []
        minimum = min(values)
        shifted = [value - minimum for value in values]
        total = sum(shifted)
        return [value / total for value in shifted] if total > 0 else [1 / len(values)] * len(values)

    def _normalized_diversity(self,population):
        values = [_as_float(getattr(pop, 'diversity', None), 0.0) for pop in population]
        if not values:
            return []
        total = sum(max(0.0, value) for value in values)
        return [max(0.0, value) / total for value in values] if total > 0 else [1 / len(values)] * len(values)
    
    def _compute_diversity(self,population):
        for i,memberA in enumerate(population):
            memberA.diversity = 0
            for memberB in self.breeding_population:
                if memberA is memberB: continue
                memberA.diversity += self._compute_distance(memberA,memberB)
        
    def _compute_distance(self,memberA,memberB):
        distance = 0
        rounds = min(len(memberA.round_functions), len(memberB.round_functions), self.num_rounds)
        for nr in range(max(0, rounds - 1)):
            distance += substitution_functions.compute_distance(
                memberA.round_functions[nr].substitution,
                memberB.round_functions[nr].substitution,
            )
            linear_a = memberA.round_functions[nr].linear
            linear_b = memberB.round_functions[nr].linear
            if linear_a is not None and linear_b is not None:
                distance += linear_functions.compute_distance(linear_a, linear_b)
        if rounds:
            distance += substitution_functions.compute_distance(
                memberA.round_functions[rounds - 1].substitution,
                memberB.round_functions[rounds - 1].substitution,
            )
        return distance

    @staticmethod
    def ismember(memberA,group):
        for memberB in group:
            if memberA.is_equal(memberB): return True
        return False

    def _force_unique_mutation(self, member, forbidden, max_attempts=128):
        """Mutate a duplicate child until its concrete cipher is unique."""
        attempts = 0
        last_mutation = None
        while self.ismember(member, forbidden):
            if attempts >= max_attempts:
                raise RuntimeError('unable to produce a unique child after forced mutation')
            last_mutation = member.mutate(prob=1.0)
            if last_mutation is None:
                raise RuntimeError('forced mutation did not produce a mutation event')
            attempts += 1
        return last_mutation, attempts

    def breeding(self, advisor=None, generation_context=None, engineering_validator=None):
        """Prepare candidate slots and let the advisor choose crossover/mutation.

        The framework does not apply a local crossover or mutation after this
        point.  Duplicate slots are reported to the advisor, which must resolve
        them with a validated action plan.
        """
        # Crossover and mutation are delegated to the LLM.  Start from a deep
        # copy of the current population so an unavailable/invalid advisor is
        # a no-op and never mutates evaluated candidates in place.
        target_size = max(0, int(config.HYPERPARAMETERS['POPULATION_SIZE']))
        self.next_members = [deepcopy(member) for member in self.members[:target_size]]
        self.last_breeding_records = [
            {
                'type': 'llm_candidate_pool',
                'child_index': index,
                'child_id': _stable_member_id(member),
                'parent_ids': [],
                'strategy': 'llm_decides',
                'status': 'pending',
            }
            for index, member in enumerate(self.next_members)
        ]
        # The LLM must decide how to resolve collisions.  Mark copied slots
        # that already equal an existing candidate so the prompt can request a
        # mutation/crossover instead of silently accepting duplicates.
        for index, child in enumerate(self.next_members):
            duplicate = self.ismember(child, self.members + self.fittest_population)
            self.last_breeding_records[index].update(
                duplicate_before_llm=bool(duplicate),
                duplicate_requires_llm_action=bool(duplicate),
            )
        if advisor is None:
            from llm_mutation import DeepSeekMutationAdvisor
            advisor = DeepSeekMutationAdvisor()
        context = dict(generation_context or {})
        context.setdefault('generation', self.gen_index)
        context.setdefault('num_rounds', self.num_rounds)
        context.setdefault('population_size', len(self.members))
        context.setdefault('elite_ids', [
            getattr(member, 'candidate_id', None) or getattr(member, 'identifier', None)
            for member in self.next_fittest_population
        ])
        context.setdefault('breeding_ids', [
            getattr(member, 'candidate_id', None) or getattr(member, 'identifier', None)
            for member in self.breeding_population
        ])
        # ``generation_context`` is created before ``breeding()`` by the main
        # loop. Refresh the child-specific fields after crossover so the LLM
        # sees the actual candidates and duplicate hints it is expected to
        # reason about.
        context['crossover_children'] = [
            {
                'candidate_id': _stable_member_id(member),
                'parent_ids': list(getattr(member, 'parent_ids', []) or []),
                'strategy': getattr(member, 'crossover_strategy', None),
                'details': deepcopy(getattr(member, 'crossover_details', {}) or {}),
                'fingerprint': _safe_member_fingerprint(member),
                'duplicate_before_llm': bool(
                    self.last_breeding_records[index].get('duplicate_before_llm', False)
                )
                if index < len(self.last_breeding_records)
                else False,
            }
            for index, member in enumerate(self.next_members)
        ]
        context['crossover_records'] = deepcopy(self.last_breeding_records)
        context['duplicate_children'] = [
            record['child_id']
            for record in self.last_breeding_records
            if record.get('duplicate_before_llm')
        ]
        try:
            if hasattr(advisor, 'mutate_generation'):
                mutated_members, mutation_report = advisor.mutate_generation(
                    self.next_members,
                    generation_context=context,
                    engineering_validator=engineering_validator,
                )
            elif callable(advisor):
                result = advisor(self.next_members, context)
                mutated_members, mutation_report = result
            else:
                mutated_members = self.next_members
                mutation_report = {
                    'status': 'fallback_noop',
                    'fallback_reason': 'invalid_advisor',
                    'change_records': [],
                }
        except Exception as exc:
            # ComponentValidationError intentionally interrupts the search after
            # three failed generations. Preserve its structured report on the
            # generation before propagating so callers can inspect the failure.
            failure_report = getattr(exc, 'report', None)
            if isinstance(failure_report, dict):
                self.last_mutation_report = deepcopy(failure_report)
            raise
        self.next_members = list(mutated_members)
        self.last_mutation_report = mutation_report or {}
        # Do not mutate duplicates locally: all crossover and mutation choices
        # belong to the LLM.  Record collisions so the next prompt can request
        # a deliberate mutation/crossover with the required structural checks.
        existing = list(self.members) + list(self.fittest_population)
        seen = list(existing)
        for child_index, child in enumerate(self.next_members):
            duplicate = self.ismember(child, seen)
            if duplicate:
                self.last_mutation_report.setdefault('warnings', []).append({
                    'candidate_index': child_index,
                    'reason': 'post_advisor_duplicate_requires_llm_action',
                })
            seen.append(child)
        for record in self.last_mutation_report.get('change_records', []):
            index = record.get('candidate_index')
            if isinstance(index, int) and 0 <= index < len(self.next_members):
                self.next_members[index].mutation_changes.append(deepcopy(record))
        return self.last_mutation_report

    def mutate(self):
        for member in self.members:
            member.mutate(prob=config.GENETIC_ALGO['MUTATION_PROB'])

    def compute_fitness(self, max_threads=1, context=None):
        tmp_members = []
        evaluation_mode = getattr(config, 'FRAMEWORK', {}).get('EVALUATION_MODE', 'legacy')
        if evaluation_mode == 'plugins':
            for member in self.members:
                member.compute_fitness(context=context)
                tmp_members.append(member)
        else:
            with ProcessPoolExecutor(max_workers=max_threads) as executor:
                futures = [executor.submit(utils.call_compute, member) for member in self.members]
                for future in futures:
                    tmp_members.append(future.result())
        self.members = sorted(tmp_members,key=lambda member: member.pop_index)
    
    def print_result(self):
        population = self.next_fittest_population or sorted(
            self.members,
            key=lambda member: _as_float(getattr(member, 'fitness', None), -float('inf')),
            reverse=True,
        )[:config.GENETIC_ALGO['NUM_FIT_CIPHERS']]
        for member in population:
            print(getattr(member, 'pop_index', None))
            print(getattr(member, 'fitness', None))
            print(getattr(member, 'security_diff', None))
            print(getattr(member, 'security_linear', None))
            print(getattr(member, 'latency', None))
            print()

    def save(self,folder):
        from team_plugins.plugin_contracts import to_builtin
        file = os.path.join(folder,'gen_%s_%s.pkl' % (self.num_rounds,self.gen_index))
        utils.pickle_dump(file,self)
        members = sorted(
            self.fittest_population + self.members,
            key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
            reverse=True,
        )[:config.HYPERPARAMETERS['POPULATION_SIZE']]
        datas = {
            'num_rounds' : self.num_rounds,
            'gen_index' : self.gen_index,
            'num_members' : self.num_member
        }
        for i,member in enumerate(members):
            mem = {
                'num_rounds' : member.num_rounds,
                'gen_index' : member.gen_index,
                'pop_index' : member.pop_index,
                'differential' : member.security_diff,
                'linear' : member.security_linear,
                'latency' : member.latency,
                'fitness' : member.fitness,
                'identifier' : member.identifier,
                'candidate_id' : getattr(member, 'candidate_id', None),
                'fingerprint' : _safe_member_fingerprint(member),
                'evaluation_status' : getattr(member, 'evaluation_status', None),
                'evaluation_error' : getattr(member, 'evaluation_error', None),
                'plugin_security' : to_builtin(getattr(member, 'plugin_security', None)),
                'plugin_validation' : to_builtin(getattr(member, 'plugin_validation', None)),
                'plugin_performance' : to_builtin(getattr(member, 'plugin_performance', None)),
            }
            datas[str(i)] = mem

        # save the meta-data
        meta_file = os.path.join(folder,'summary_%s_%s.json' % (self.num_rounds,self.gen_index))
        
        with open(meta_file, "w", encoding='utf-8') as f:
            json.dump(to_builtin(datas), f, indent=4, ensure_ascii=False)

    @staticmethod
    def _trail_endpoint_available(trails, endpoint):
        """Return whether a legacy trail collection has a usable endpoint.

        Team B's placeholder/plugin results intentionally leave ``diff_trails``
        and ``linear_trails`` unset.  The original steal implementation expects
        a populated ``trail`` object and asserts when it is absent.  Keep this
        check deliberately narrow so legacy trail objects retain their original
        behavior while plugin generations can select the safe random expansion.
        """
        if not isinstance(trails, (list, tuple)) or not trails:
            return False
        try:
            trail_item = trails[0] if endpoint == 'before' else trails[-1]
            values = getattr(trail_item, endpoint, None)
            if values is None or len(values) == 0:
                return False
            vector = values[0] if endpoint == 'before' else values[-1]
            return vector is not None and len(vector) > 0
        except (AttributeError, IndexError, TypeError, ValueError):
            return False

    @classmethod
    def _can_steal_one_round(cls, member, source_members=None):
        """Check the minimum state required by ``round_function.steal_one_round``."""
        parity = int(getattr(member, 'num_rounds', 0)) % 2
        endpoint = 'before' if parity == 0 else 'after'
        if not cls._trail_endpoint_available(getattr(member, 'diff_trails', None), endpoint):
            return False
        if not cls._trail_endpoint_available(getattr(member, 'linear_trails', None), endpoint):
            return False

        # The steal implementation also needs at least one source round with a
        # linear matrix.  This is normally guaranteed by randomized candidates,
        # but checking it avoids an empty ``np.random.choice`` in plugin runs.
        if source_members is not None:
            for source in source_members:
                for round_function in getattr(source, 'round_functions', []) or []:
                    linear = getattr(round_function, 'linear', None)
                    if linear is not None and getattr(linear, 'matrix', None) is not None:
                        return True
            return False
        return True

    def next_gen(self,max_threads=1):
        print('moving on to the next generation')
        print('current num rounds: %s, gen_index: %s' % (self.num_rounds,self.gen_index))
        # Do not leak a previous round-growth report into a normal generation
        # transition.  The report is populated again only in the growth branch.
        self.last_round_growth_report = {}

        # terminating condition
        if self.gen_index == config.HYPERPARAMETERS['MAX_GENERATION'][self.num_rounds] - 1 and self.num_rounds == config.HYPERPARAMETERS['MAX_NUM_ROUNDS']:
            self.members = sorted(
                self.fittest_population + self.members,
                key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
                reverse=True,
            )[:config.HYPERPARAMETERS['POPULATION_SIZE']]
            self.next_fittest_population = []
            self.fittest_population = []
            self.breeding_population = []
            self.next_members = []
            return 0
        # continue to the next generation
        elif self.gen_index < config.HYPERPARAMETERS['MAX_GENERATION'][self.num_rounds] - 1: 
            self.gen_index += 1
                
            # main members
            self.members = self.next_members
            self.next_members = []
            for index,member in enumerate(self.members):
                member.gen_index = self.gen_index
                member.pop_index = index
                member.candidate_id = 'r%02d-g%04d-p%04d' % (
                    self.num_rounds, self.gen_index, index
                )
                member.is_elite = False
            self.fittest_population = self.next_fittest_population
            self.next_fittest_population = []
            self.breeding_population = []
            self.num_member = len(self.members)

        else: # add one more round
            self.num_rounds += 1
            self.members = sorted(
                self.fittest_population + self.members,
                key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
                reverse=True,
            )[:config.HYPERPARAMETERS['POPULATION_SIZE']]
            self.next_members = []
            self.next_fittest_population = []
            self.fittest_population = []
            self.breeding_population = []
            self.gen_index = 0
            
            # Round growth is deliberately independent of crossover/LLM choice:
            # every candidate receives one freshly randomized linear layer and
            # one freshly randomized S-box layer.  The old final round gains the
            # new inter-round linear layer; the appended round is final and thus
            # has no following linear layer.
            members = deepcopy(self.members)
            tmp_members = []
            for member in members:
                if not member.round_functions:
                    member.randomize(self.num_rounds)
                previous_final = member.round_functions[-1]
                new_linear = components.linear_layer()
                new_linear.randomize()
                previous_final.linear = new_linear

                new_round = components.round_function()
                new_round.randomize()
                new_round.linear = None
                member.add_round_function(new_round)
                tmp_members.append(member)

            self.last_round_growth_report = {
                'strategy': 'random_sbox_and_linear',
                'member_count': len(tmp_members),
            }

            round_growth_members = []

            for i,member in enumerate(tmp_members):
                member.gen_index = self.gen_index
                member.pop_index = i
                member.candidate_id = 'r%02d-g%04d-p%04d' % (
                    self.num_rounds, self.gen_index, i
                )
                member.is_elite = False
                _reset_evaluation_state(
                    member, status='pending', clear_mutation_changes=True
                )
                round_growth_members.append({
                    'member_index': int(i),
                    'source_member_id': _stable_member_id(self.members[i]) if i < len(self.members) else None,
                    'member_id': member.candidate_id,
                    'requested': 'RANDOM_SBOX_AND_LINEAR',
                    'effective': 'RANDOM_SBOX_AND_LINEAR',
                    'status': 'applied',
                })

            self.last_round_growth_report['members'] = round_growth_members

            self.members = tmp_members
            self.num_member = len(self.members)
            # check if prince is in
            for i, member in enumerate(self.members):
                if member.identifier == 'PRINCE':
                    print('Prince is still in the pool. Replacing with a higher number of rounds')
                    member.get_prince(self.num_rounds)
                    break

        print('next num rounds: %s, gen_index: %s' % (self.num_rounds,self.gen_index))
        return 1

    def bruteforce_expand_pop(self,num_expanded_pop):
        # adding one round 
        self.next_members = []

        for i in range(num_expanded_pop):
            member = deepcopy(self.members[i % self.num_member])
            member.smart_randomize_one_round()
            member.pop_index = i
            self.next_members.append(member)
        self.members = self.next_members
        self.num_member = num_expanded_pop
        self.next_members = []

    def bruteforce_reduce_pop(self,num_pop):
        self.members = sorted(self.members,key=lambda x: x.fitness, reverse=True)[:num_pop]
        self.num_member = num_pop
