"""Entry points for the uKNIT genetic search.

The orchestration layer keeps the original evaluation/selection/crossover loop,
but delegates component mutation to the batched DeepSeek planner. The planner
is created in the parent process and never attached to a Member, so legacy
ProcessPoolExecutor evaluation remains pickle-safe.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
from typing import Any

from cipher.Ciphers import Generation
from config import BRUTEFORCE, FRAMEWORK, GENETIC_ALGO, HYPERPARAMETERS, INIT_SETTINGS
import config
import utils
from iteration_logger import IterationLogger, population_summary, record_changes
from seed_config import SEED, set_global_seed


PROJECT_ROOT = Path(__file__).resolve().parent
set_global_seed(SEED)


def _new_run_folder() -> Path:
    runs_root = PROJECT_ROOT / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
    folder = runs_root / ("RUN_" + timestamp)
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _prepare_run(folder: Path) -> tuple[Path, IterationLogger]:
    folder.mkdir(parents=True, exist_ok=True)
    utils.create_necessary_folders(str(folder))
    for filename in ("config.py", "deepseek_config.py", "seed_config.py"):
        source = PROJECT_ROOT / filename
        if source.exists():
            shutil.copy2(source, folder / filename)
    (folder / "seed.txt").write_text(
        "Global seed set to: %s\n" % SEED,
        encoding="utf-8",
    )
    logger = IterationLogger(folder / "logs", filename="iteration_log.jsonl")
    return folder, logger


def _member_id(member: Any) -> str | None:
    value = getattr(member, "candidate_id", None)
    if value:
        return str(value)
    identifier = getattr(member, "identifier", None)
    if identifier:
        return str(identifier)
    generation = getattr(member, "gen_index", None)
    population = getattr(member, "pop_index", None)
    if generation is not None and population is not None:
        return "g%s-p%s" % (generation, population)
    return None


def _compact_member(member: Any) -> dict[str, Any]:
    result = {
        "candidate_id": _member_id(member),
        "identifier": getattr(member, "identifier", None),
        "generation": getattr(member, "gen_index", None),
        "population_index": getattr(member, "pop_index", None),
        "fitness": getattr(member, "fitness", None),
        "diversity": getattr(member, "diversity", None),
        "security_diff": getattr(member, "security_diff", None),
        "security_linear": getattr(member, "security_linear", None),
        "latency": getattr(member, "latency", None),
        "evaluation_status": getattr(member, "evaluation_status", None),
        "evaluation_error": getattr(member, "evaluation_error", None),
        "plugin_security": getattr(member, "plugin_security", None),
        "plugin_validation": getattr(member, "plugin_validation", None),
        "plugin_performance": getattr(member, "plugin_performance", None),
        "is_elite": getattr(member, "is_elite", False),
        "parent_ids": list(getattr(member, "parent_ids", []) or []),
        "crossover_strategy": getattr(member, "crossover_strategy", None),
        "crossover_details": getattr(member, "crossover_details", {}),
        "mutation_changes": list(getattr(member, "mutation_changes", []) or []),
    }
    try:
        result["fingerprint"] = member.candidate_fingerprint()
    except Exception as exc:
        result["fingerprint_error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def _generation_context(generation: Generation, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "generation": generation.gen_index,
        "num_rounds": generation.num_rounds,
        "evaluation_mode": FRAMEWORK.get("EVALUATION_MODE", "legacy"),
        "population": [_compact_member(member) for member in generation.members],
        "elite": [_compact_member(member) for member in generation.next_fittest_population],
        "breeding_population": [
            _compact_member(member)
            for member in getattr(generation, "breeding_population", [])
        ],
        "crossover_children": [
            {
                "candidate_id": _member_id(member),
                "parents": getattr(member, "parent_ids", []),
                "strategy": getattr(member, "crossover_strategy", None),
                "details": getattr(member, "crossover_details", {}),
                "mutation_changes": getattr(member, "mutation_changes", []),
            }
            for member in getattr(generation, "next_members", [])
        ],
    }


def _build_advisor(advisor: Any = None) -> Any:
    if advisor is not None:
        return advisor
    from llm_mutation import DeepSeekMutationAdvisor

    return DeepSeekMutationAdvisor()


def _engineering_validator() -> Any:
    if FRAMEWORK.get("EVALUATION_MODE", "legacy") != "plugins":
        return None
    from team_plugins.plugin_loader import validate_candidate

    return validate_candidate


def run(
    init: bool = True,
    generation: Generation | None = None,
    *,
    advisor: Any = None,
    run_folder: str | os.PathLike[str] | None = None,
    max_iterations: int | None = None,
) -> Generation | None:
    """Run the search and return the final generation.

    ``max_iterations`` is useful for smoke tests and does not alter the normal
    configured stopping condition when omitted.
    """

    folder = Path(run_folder) if run_folder is not None else _new_run_folder()
    folder, logger = _prepare_run(folder)
    run_id = folder.name
    mutation_advisor = _build_advisor(advisor)  # 下一代
    validator = _engineering_validator()        # 合法性验证

    num_pop = int(HYPERPARAMETERS["POPULATION_SIZE"])
    num_threads = int(HYPERPARAMETERS["NUM_OF_THREADS"])
    num_fittest = int(GENETIC_ALGO["NUM_FIT_CIPHERS"])
    num_breeding = int(
        round(GENETIC_ALGO["BREEDING_POPULATION_PROPORTION"] * num_pop)
    )                                          # 繁殖池数量
    if num_pop > 1:
        num_breeding = max(2, num_breeding)

    if init:
        generation = Generation(INIT_SETTINGS["INIT_NUM_ROUNDS"], 0)
        generation.randomize(num_pop)
        if INIT_SETTINGS["INCLUDE_PRINCE"] and generation.members:
            generation.members[0].identifier = "PRINCE"
            generation.members[0].get_prince(INIT_SETTINGS["INIT_NUM_ROUNDS"])
        if INIT_SETTINGS["INCLUDE_UKNIT"]:
            for index, window in enumerate(range(0, 9)):
                target_index = index + 1
                if target_index >= len(generation.members):
                    break
                generation.members[target_index].identifier = "UKNIT_%s" % window
                generation.members[target_index].get_uknitbc(
                    INIT_SETTINGS["INIT_NUM_ROUNDS"], window=window
                )
    if generation is None:
        raise ValueError("generation must be provided when init=False")

    iteration_index = 0
    while generation is not None:
        evaluation_context = {
            "run_id": run_id,
            "work_dir": str(PROJECT_ROOT),
            "generation": generation.gen_index,
        }
        generation.compute_fitness(num_threads, context=evaluation_context)
        generation.print_result()
        utils.optimize_save()
        generation.save(str(folder))

        evaluated_population = population_summary(generation)
        evaluated_generation_index = generation.gen_index
        evaluated_num_rounds = generation.num_rounds

        generation.select_fittest_population(num_fittest)
        generation.select_breeding_population(num_breeding)
        context = _generation_context(generation, run_id)
        mutation_report = generation.breeding(
            advisor=mutation_advisor,
            generation_context=context,
            engineering_validator=validator,
        )

        changes = list(getattr(generation, "last_breeding_records", []))
        changes.extend(mutation_report.get("change_records", []))
        crossover_population = population_summary(generation.next_members)
        iteration_index += 1

        reached_iteration_limit = (
            max_iterations is not None
            and iteration_index >= max(0, int(max_iterations))
        )
        transition_result = None
        if not reached_iteration_limit:
            transition_result = generation.next_gen(num_threads)

        logger.log_generation(
            generation,
            record_changes(changes),
            population=evaluated_population,
            generation_index=evaluated_generation_index,
            num_rounds=evaluated_num_rounds,
            iteration_index=iteration_index - 1,
            metadata={
                "run_id": run_id,
                "evaluation_mode": FRAMEWORK.get("EVALUATION_MODE", "legacy"),
                "evaluated_generation_index": evaluated_generation_index,
                "evaluated_num_rounds": evaluated_num_rounds,
                "evaluated_population": evaluated_population,
                "mutation_report": mutation_report,
                "next_population": crossover_population,
                "round_growth_report": getattr(generation, "last_round_growth_report", {}),
                "transition_result": transition_result,
            },
        )

        if reached_iteration_limit or transition_result == 0:
            break
    return generation


def run_bruteforce() -> Generation | None:
    """Preserve the original brute-force expansion entry point."""

    folder = _new_run_folder()
    folder, _logger = _prepare_run(folder)
    num_pop = int(HYPERPARAMETERS["POPULATION_SIZE"])
    num_threads = int(HYPERPARAMETERS["NUM_OF_THREADS"])
    generation = Generation(INIT_SETTINGS["INIT_NUM_ROUNDS"], 0)
    generation.randomize(num_pop)
    while True:
        generation.bruteforce_expand_pop(BRUTEFORCE["EXPANDED_POPULATION_SIZE"])
        generation.compute_fitness(num_threads)
        generation.print_result()
        generation.bruteforce_reduce_pop(num_pop)
        utils.optimize_save()
        generation.save(str(folder))
        if generation.num_rounds == HYPERPARAMETERS["MAX_NUM_ROUNDS"]:
            break
    return generation


def start_from(file: str | os.PathLike[str]) -> Generation | None:
    print("start with file %s" % file)
    return run(init=False, generation=read(file))


def read(file: str | os.PathLike[str]) -> Generation:
    return utils.pickle_load(file)


if __name__ == "__main__":
    run()
