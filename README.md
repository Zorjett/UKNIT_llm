# uKNIT Construction Framework

This directory is the Team A integration scaffold for the uKNIT structural
search. It preserves the evaluation and population-selection shape while
delegating structural changes to the LLM:

1. Evaluate the current population.
2. Retain elite candidates and build a diversity-aware breeding pool.
3. Give the current candidate pool and parent bindings to the LLM planner.
4. Let the LLM choose validated crossover and mutation actions.
5. Form the next generation and apply the existing round-growth policy.

The production breeding path has no random ``Member.breed`` or probability-based
mutation API. Crossover and mutation actions are selected by the LLM and then
materialized and structurally validated locally.
If a candidate slot is a duplicate of an existing candidate, the planner
request marks that slot as requiring an action. An empty plan or an action that
does not change its fingerprint is rejected and regenerated; after three
failed attempts the run stops with a structured validation error.
The initial MANTIS S-box and permutation setup, legacy evaluators, result
saving, and round-growth flow are kept for compatibility.

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
  LLM-guided search loop continues. These runs are smoke/integration runs, not cipher
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

Results and configuration snapshots are created below runs/. By default each
process chooses a fresh seed, so separate runs start from different initial
populations. To reproduce a run, read its `seed.txt` and set UKNIT_SEED before
starting:

~~~powershell
$env:UKNIT_SEED = "20260831"
python main.py
~~~

config.py controls population size, generation limits, initialization, round
growth, and evaluator mode. Structural crossover and mutation probabilities
are not configured because those decisions belong to the LLM planner. Use
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
candidate/population summaries and returns constrained ``actions``. To keep
per-generation cost bounded, prompts contain each candidate's fitness,
differential/linear security scores, latency, evaluation status, fingerprint,
and compact layer digests; they do not contain full S-box tables or 64x64
linear matrices. Linear mutations should use short row/column-swap actions.
The request report records ``prompt_chars`` and a conservative
``prompt_token_estimate`` for monitoring. The framework validates action fields, candidate fingerprints, S-box
permutations, and linear-layer structure before applying a change. When the
model produces an illegal component or mutation plan, it receives the
validation issues and is asked to regenerate, up to three complete generation
attempts per round. If all three attempts remain invalid, the run stops with a
component-validation error instead of accepting an invalid candidate. Failed
API calls remain structured no-op reports.

Each candidate also carries a bounded history of accepted structural actions
and fingerprints. Repeating a recent action or recreating a previously visited
cipher is rejected and sent back to the LLM for a different decision. Summary
files contain only the current evaluated generation; a previous elite cannot
replace a current member merely because their fitness values are equal.

S-box decisions use the compact `bit_permutation` field, such as
`[1,0,2,3]`; the framework expands it to the 4x4 matrix locally. Schema
rejection reasons are printed while the run is active. If all attempts fail,
the complete actions and validation feedback are saved as
`llm_failure_r*_g*.json` in the current run directory.

## Team B/C plugin handoff

The fixed handoff point is team_plugins/README.md. Team B replaces
team_plugins/security_evaluator.py; Team C replaces
team_plugins/engineering_evaluator.py. Keep the documented public function
names and PLUGIN_API_VERSION = "1.0". No main-framework change is needed after
replacement.

The loader can alternatively import packaged implementations through
UKNIT_SECURITY_PLUGIN and UKNIT_ENGINEERING_PLUGIN, but replacing the two
default files is the simplest handoff path.

## OpenLane latency measurement

Team C performance analysis is enabled by default in plugin mode. When
enabled, each candidate is materialized as Verilog and evaluated independently
by OpenLane; the measured critical-path delay is written to `latency` in
nanoseconds. If OpenLane cannot be started, exits unsuccessfully, or produces
no timing result, the run stops with an error. No placeholder latency is used
in this mode.

Set `OPENLANE['ENABLED']` to `False`, or set
`UKNIT_OPENLANE_ENABLED=false`, to skip OpenLane for fast search/smoke runs.
In that mode each candidate receives `latency=1.0` with units marked as
`placeholder`; security and structural validation still run normally, and no
OpenLane process, Docker container, or work directory is created.

On Windows, the supported setup is Docker Desktop with WSL2 integration and
OpenLane 2 installed in WSL.  The evaluator first tries a native `openlane`,
then explicitly invokes the OpenLane executable in the `Ubuntu-24.04` WSL
distribution (the default path is
`/home/mnzn/venvs/openlane/bin/openlane`).  The flow stops after
`OpenROAD.STAPostPNR`, which is the stage that produces the real timing
measurement; later layout export/DRC stages are not needed for latency and are
skipped.  Linux can use a native OpenLane installation.  Set
`UKNIT_OPENLANE_COMMAND` when a site-specific command is required, for example
`openlane --dockerized {config}`.  Optional settings are
`UKNIT_OPENLANE_WORK_ROOT`, `UKNIT_OPENLANE_TIMEOUT_SECONDS`, and
`UKNIT_OPENLANE_CLOCK_PERIOD_NS`.  OpenLane stdout, stderr, generated Verilog,
and reports are retained under the configured work root for diagnosis.
During a run, the terminal prints a candidate-level progress line, an
OpenLane start message, a 30-second heartbeat while OpenLane is working, and
the measured latency when it finishes. OpenLane output itself is also saved in
`openlane.stdout.log` and `openlane.stderr.log` under that candidate's work
directory.

From WSL, run the project with the OpenLane virtual-environment interpreter so
the same Python installation is used for both the project and OpenLane:

```bash
cd /mnt/d/xinxijishujingsai/UKNIT_llm_v2/UKNIT_llm-main
/home/mnzn/venvs/openlane/bin/python main.py
```

If the virtual environment is activated, `python3 main.py` is equivalent. The
entry point also adds its own project directory to `sys.path`, so local
packages such as `cipher` remain importable from WSL mounted drives.

## Legacy prerequisites

The default placeholder/plugin workflow does not run the original SAT/Yosys
evaluation tools. For legacy mode, install and expose the following tools in
PATH, or configure their paths in config.py:

- Kissat, or a compatible SAT solver;
- Espresso; and
- the original Yosys/Python runtime required by the legacy evaluator.
