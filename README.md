# uKNIT Construction Framework

This directory is the Team A integration scaffold for the uKNIT genetic
search. It preserves the original search shape:

1. Evaluate the current population.
2. Retain elite candidates and build a diversity-aware breeding pool.
3. Crossover two breeding-pool parents with SINGLE, DOUBLE, or UNIFORM.
4. Ask the LLM for validated mutations of crossover children only.
5. Form the next generation and apply the existing round-growth policy.

SINGLE, DOUBLE, and UNIFORM remain crossover strategies. They are not the
replaced random mutation strategies. The initial MANTIS S-box and permutation
setup, legacy evaluators, result saving, and round-growth flow are kept for
compatibility.

## Framework status

The checked-in repository is intentionally runnable before Teams B and C
deliver their implementations:

- config.py defaults to EVALUATION_MODE=plugins.
- The Team B and Team C files in team_plugins/ are contract-valid placeholders.
  They do not invent security or performance measurements.
- deepseek_config.py intentionally contains an empty API key and an empty
  model. With either value empty, the LLM planner makes no HTTP request and
  preserves the crossover children unchanged.
- In either unavailable case, evaluation uses neutral fitness (0.0) and the
  genetic loop continues. These runs are smoke/integration runs, not cipher
  security or latency results.

The iteration audit log is written to
runs/RUN_*/logs/iteration_log.jsonl. It records population summaries,
crossover provenance, LLM request/fallback information, and accepted or
rejected mutation records.

## Quick start

Install the Python dependencies from this directory:

~~~bash
python -m pip install -r requirements.txt
python main.py
~~~

Results and configuration snapshots are created below runs/. To reproduce the
initial population and crossover randomness, set UKNIT_SEED before starting:

~~~powershell
$env:UKNIT_SEED = "20260831"
python main.py
~~~

config.py controls population size, generation limits, initialization,
crossover proportions, round growth, and evaluator mode. Use
UKNIT_EVALUATION_MODE=legacy only when the original SAT/Yosys toolchain is
installed; legacy evaluation still needs Kissat, Espresso, and its original
runtime dependencies.

## uKNIT baseline file

When `INIT_SETTINGS['INCLUDE_UKNIT']` is enabled, the original code tries to
load the precomputed full uKNIT-BC cipher from `uknit64_cipher.pkl`. This file
is a data artifact, not a pip dependency, and it is not included in this
repository. Set `UKNIT_BASELINE_PATH` to its absolute path (or place it in the
project root) when the published baseline is available. By default, a missing
file falls back to a fresh random candidate with the same round shape; this is
useful for smoke/search runs but does not reproduce a published uKNIT window.
Set `UKNIT_FALLBACK_RANDOM=false` to require the baseline and fail with a
clear error when it is absent.

## DeepSeek configuration

All DeepSeek settings live in deepseek_config.py. The checked-in local values
are deliberately blank:

~~~python
LOCAL_DEEPSEEK_API_KEY = ""
LOCAL_DEEPSEEK_MODEL = ""
~~~

Set both values in that file for a local experiment, or preferably supply
DEEPSEEK_API_KEY and DEEPSEEK_MODEL as environment variables. Environment
variables take precedence. DEEPSEEK_ENABLED=false explicitly disables the
advisor. An enabled advisor still no-ops safely if the key or model is absent;
the per-generation log records missing_api_key, missing_model, or disabled as
the fallback reason.

The LLM never receives a Member object. It receives JSON-compatible
candidate/population summaries and returns a constrained mutation plan. The
framework validates all operations, candidate fingerprints, S-box
permutations, and linear-layer structure before applying a change. When the
model produces an illegal component or mutation plan, it receives the
validation issues and is asked to regenerate, up to three complete generation
attempts per round. If all three attempts remain invalid, the run stops with a
component-validation error instead of accepting an invalid candidate. Failed
API calls remain structured no-op reports.

## Team B/C plugin handoff

The fixed handoff point is team_plugins/README.md. Team B replaces
team_plugins/security_evaluator.py; Team C replaces
team_plugins/engineering_evaluator.py. Keep the documented public function
names and PLUGIN_API_VERSION = "1.0". No main-framework change is needed after
replacement.

The loader can alternatively import packaged implementations through
UKNIT_SECURITY_PLUGIN and UKNIT_ENGINEERING_PLUGIN, but replacing the two
default files is the simplest handoff path.

## Legacy prerequisites

The default placeholder/plugin workflow does not run the original SAT/Yosys
evaluation tools. For legacy mode, install and expose the following tools in
PATH, or configure their paths in config.py:

- Kissat, or a compatible SAT solver;
- Espresso; and
- the original Yosys/Python runtime required by the legacy evaluator.
