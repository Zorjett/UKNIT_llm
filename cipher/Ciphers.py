'''
class Member: Contains the data structure of a cipher, including properties useful in the genetic algorithm.
class Generation: Contains info about the current generation
'''

from cipher.linear_functions import *
from cipher.sbox_functions import *
import cipher.components as components

import utils
import numpy as np
import config
from copy import deepcopy
from concurrent.futures import ProcessPoolExecutor  # compatibility symbol for external callers/tests
import json
import warnings
from team_plugins.openlane_performance import OpenLaneLatencyError
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


def _member_llm_history(member, max_fingerprints=16, max_actions=8):
    """Return compact novelty constraints inherited with one candidate."""
    fingerprints = []
    action_specs = []
    for change in getattr(member, 'mutation_changes', []) or []:
        if not isinstance(change, dict) or change.get('status') != 'accepted':
            continue
        for key in ('before_fingerprint', 'after_fingerprint'):
            value = change.get(key)
            if value and value not in fingerprints:
                fingerprints.append(str(value))
        action_spec = change.get('action_spec')
        if isinstance(action_spec, dict) and action_spec not in action_specs:
            action_specs.append(deepcopy(action_spec))
    current = _safe_member_fingerprint(member)
    if current and not str(current).startswith('error:') and current not in fingerprints:
        fingerprints.append(current)
    return {
        'forbidden_fingerprints': fingerprints[-max_fingerprints:],
        'recent_action_specs': action_specs[-max_actions:],
    }


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
        # Metadata is deliberately plain Python data so Member remains pickleable.
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

    def add_round_function(self,round_function):
        round_function.round_index = self.num_rounds
        round_function.substitution.round_index = self.num_rounds
        self.round_functions.append(round_function)
        self.num_rounds += 1

    def compute_fitness(self, context=None):
        """Evaluate this member through the active plugin interfaces."""
        mode = getattr(config, 'FRAMEWORK', {}).get('EVALUATION_MODE', 'plugins')
        if mode != 'plugins':
            raise RuntimeError(
                'Only plugin evaluation is supported; '
                'set FRAMEWORK["EVALUATION_MODE"] to "plugins".'
            )
        return self.compute_plugin_fitness(context=context)

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
            if bool(config.OPENLANE.get('ENABLED', True)):
                try:
                    performance_result = evaluate_performance(candidate, base_context)
                except Exception as exc:
                    # Any OpenLane infrastructure or parsing failure is fatal.
                    # It must never be converted into a neutral/placeholder
                    # fitness while real performance analysis is enabled.
                    if isinstance(exc, OpenLaneLatencyError):
                        raise
                    raise OpenLaneLatencyError(
                        'OpenLane performance evaluation failed: %s' % exc
                    ) from exc
            else:
                # Keep the normal plugin contract shape while explicitly
                # recording that OpenLane was disabled by configuration.
                performance_result = {
                    'schema_version': '1.0',
                    'plugin_api_version': '1.0',
                    'plugin_name': 'team-c-engineering-openlane',
                    'candidate_id': candidate.get('candidate_id', 'unknown'),
                    'status': 'ok',
                    'valid': True,
                    'warnings': ['OpenLane performance analysis is disabled by configuration.'],
                    'errors': [],
                    'artifacts': {},
                    'metrics': {
                        'latency': 1.0,
                        'area': None,
                        'energy': None,
                        'units': {'latency': 'placeholder'},
                    },
                }
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
            performance_status = str(
                performance_result.get('status', '')
                if isinstance(performance_result, dict) else ''
            ).lower()
            if performance_status != 'ok':
                raise OpenLaneLatencyError(
                    'OpenLane performance evaluation did not return status=ok: %s'
                    % performance_status
                )
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
            if not np.isfinite(self.latency) or self.latency <= 0:
                raise OpenLaneLatencyError('OpenLane performance result is missing a finite positive latency')

            if unavailable or invalid or not plugin_accepts_candidate:
                self.fitness = 0.0
                self.evaluation_status = 'unavailable' if unavailable else 'invalid'
            else:
                sec_values = self.security_diff + [2 * value for value in self.security_linear]
                security_score = min(sec_values) if sec_values else _as_float(
                    security_result.get('score', 0.0), default=0.0
                )
                latency = max(self.latency, 1.0)
                window = min(self.num_rounds, len(config.FITNESS_SETTINGS['FITNESS_FORMULA']) - 1)
                formula = config.FITNESS_SETTINGS['FITNESS_FORMULA'][window]
                self.fitness = float(formula(security_score, latency, self.num_rounds))
                self.evaluation_status = 'ok'
            self.evaluation_error = None
        except Exception as exc:
            # A real OpenLane measurement is mandatory. Do not convert a
            # missing/failed timing run into a neutral candidate; stop the
            # search so the caller sees the infrastructure error immediately.
            if type(exc).__name__ in {'OpenLaneLatencyError', 'TimeoutExpired'}:
                raise
            self.security_diff = []
            self.security_linear = []
            self.latency = 0.0
            self.fitness = 0.0
            self.evaluation_status = 'error'
            self.evaluation_error = '%s: %s' % (type(exc).__name__, exc)
        return self.fitness
    
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


    def is_equal(self,member):
        for i in range(self.num_rounds):
            if not self.round_functions[i].is_equal(member.round_functions[i]):
                return False
        return True
 
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
        self.last_breeding_records = []
        self.last_mutation_report = {}
        self.last_round_growth_report = {}

    def __setstate__(self, state):
        self.__dict__.update(state)
        defaults = {
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
        # Python's sort is stable. Put current members first so an equal score
        # never causes a structurally newer LLM result to lose to an old elite.
        pool = self.members + self.fittest_population
        self.next_fittest_population = sorted(
            pool, key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')), reverse=True
        )[:max(0, int(num_fittest_population))]
        # adjust back
        for index in range(len(self.next_fittest_population)):
            self.next_fittest_population[index] = deepcopy(self.next_fittest_population[index])
            self.next_fittest_population[index].is_elite = True

    @staticmethod
    def ismember(memberA,group):
        for memberB in group:
            if memberA.is_equal(memberB): return True
        return False

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
        # One batched LLM decision produces the complete next population. Every
        # child slot must therefore be addressed by at least one mutation or
        # crossover action, even when the copied source was not a duplicate.
        context['required_action_children'] = [
            record['child_id'] for record in self.last_breeding_records
        ]
        context['candidate_history'] = {
            _stable_member_id(member): _member_llm_history(member)
            for member in self.next_members
            if _stable_member_id(member)
        }
        try:
            if not hasattr(advisor, 'mutate_generation'):
                raise TypeError(
                    'advisor must implement the mutate_generation() action interface'
                )
            mutated_members, mutation_report = advisor.mutate_generation(
                self.next_members,
                generation_context=context,
                engineering_validator=engineering_validator,
            )
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

    def compute_fitness(self, max_threads=1, context=None):
        """Evaluate every member through the active plugin interfaces."""
        tmp_members = []
        total = len(self.members)
        for index, member in enumerate(self.members, start=1):
            print(
                '[progress] evaluating candidate %d/%d (generation=%s, population=%s)' % (
                    index,
                    total,
                    self.gen_index,
                    getattr(member, 'pop_index', index - 1),
                ),
                flush=True,
            )
            member.compute_fitness(context=context)
            security_result = getattr(member, 'plugin_security', None)
            if (
                isinstance(security_result, dict)
                and str(security_result.get('status', '')).lower() != 'ok'
            ):
                details = security_result.get('errors') or security_result.get('warnings') or []
                print(
                    '[security] candidate %d/%d status=%s details=%s' % (
                        index,
                        total,
                        security_result.get('status', 'unknown'),
                        details,
                    ),
                    flush=True,
                )
            print(
                '[progress] candidate %d/%d complete: latency=%s ns, fitness=%s' % (
                    index,
                    total,
                    getattr(member, 'latency', None),
                    getattr(member, 'fitness', None),
                ),
                flush=True,
            )
            tmp_members.append(member)
        self.members = sorted(tmp_members, key=lambda member: member.pop_index)
    def print_result(self):
        population = self.next_fittest_population or sorted(
            self.members,
            key=lambda member: _as_float(getattr(member, 'fitness', None), -float('inf')),
            reverse=True,
        )[:config.SEARCH_SETTINGS['NUM_FIT_CIPHERS']]
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
        # A summary describes the generation named in its filename.  Including
        # the previous elite pool here made equal-fitness runs repeatedly save
        # the old generation even after the LLM had changed the current member.
        members = sorted(
            self.members,
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

    def next_gen(self,max_threads=1):
        print('moving on to the next generation')
        print('current num rounds: %s, gen_index: %s' % (self.num_rounds,self.gen_index))
        # Do not leak a previous round-growth report into a normal generation
        # transition.  The report is populated again only in the growth branch.
        self.last_round_growth_report = {}

        # terminating condition
        if self.gen_index == config.HYPERPARAMETERS['MAX_GENERATION'][self.num_rounds] - 1 and self.num_rounds == config.HYPERPARAMETERS['MAX_NUM_ROUNDS']:
            self.members = sorted(
                self.members + self.fittest_population,
                key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
                reverse=True,
            )[:config.HYPERPARAMETERS['POPULATION_SIZE']]
            self.next_fittest_population = []
            self.fittest_population = []
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
            self.num_member = len(self.members)

        else: # add one more round
            self.num_rounds += 1
            self.members = sorted(
                self.members + self.fittest_population,
                key=lambda x: _as_float(getattr(x, 'fitness', None), -float('inf')),
                reverse=True,
            )[:config.HYPERPARAMETERS['POPULATION_SIZE']]
            self.next_members = []
            self.next_fittest_population = []
            self.fittest_population = []
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
