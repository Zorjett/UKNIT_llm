# Security Analysis and Reproducible Environment

This document is the project-specific integration of the Team B security
delivery. It describes what the code actually evaluates; it does not turn the
paper's reference numbers into scores for generated candidates.

## Scope and security goal

The integrated evaluator measures the minimum integer weight of a **single
differential trail** and a **single linear trail** through the complete 64-bit
candidate. It supports 3 through 12 rounds. A candidate round is represented as
`ARK -> S -> L`, except that the last round omits `L` and is followed by the
final `ARK`. The key additions do not change difference or mask propagation,
so round keys are recorded in the contract but are not modeled as variables.

The analysis is deliberately narrower than a full cipher proof. It does not
claim differential-hull or linear-hull bounds, integral security, related-key
security, key-recovery resistance, implementation side-channel resistance, or
an exhaustive full-round proof beyond the modeled single-path search.

The threat model is therefore: a standard single-key attacker seeking one
non-zero input difference or mask that propagates through the modeled S-box
and linear layers with minimum accumulated `-log2` weight. The input endpoint
is `nonzero_free`; the output endpoint is `free`.

## Candidate contract and assumptions

The Team A candidate is accepted through the unchanged public API and converted
by `src/llm_cipher/security/team_a_adapter.py` to `candidate-spec-v0.3`.
The adapter still accepts the frozen four-round `candidate-spec-v0.2` fixture.

The following are checked before SAT is started:

- 64-bit state, MSB0 bit numbering, left-to-right hexadecimal nibbles;
- 16 four-bit bijective S-boxes per round;
- each S-box is a MANTIS input/output bit-permutation variant;
- 64 linear rows on every non-final round, with unique in-range indices;
- GF(2) rank 64 for every non-final linear layer;
- the final round has no linear layer;
- the semantic candidate SHA-256 matches the supplied hash;
- no unknown or malformed fields are silently repaired.

The hash excludes candidate IDs, fitness values, parent metadata and other
non-semantic fields. It sorts indices inside a matrix row but never reorders
rounds or S-box positions.

## Analysis method

`src/llm_cipher/security/ddt_lat.py` computes the complete 4-bit DDT and full
Walsh table. A non-zero transition has an exact integer weight only when the
corresponding probability or absolute correlation is a power-of-two fraction.
Zero transitions are impossible or zero-correlation and are not assigned a
finite weight.

`model_adapter.py` builds a bounded DIMACS CNF model for either mode. The
public query is inclusive: `total_weight <= k`. Differential layers use
`y = Mx`; linear layers use the transpose relation `alpha = M^T beta`.
`optimizer.py` first finds an upper bound by exponential thresholds and then
refines it by binary search. A SAT result contributes only a validated upper
bound. Only an explicit UNSAT result contributes a lower bound. This is an
important correction over the legacy evaluator, whose old
`analysis/security_computation.py` maps timeout or unrecognised solver output
to `unsat`; that legacy behavior is documented but is not used by the plugin
path.

`solver_runner.py` invokes Kissat without a shell, records stdout/stderr and
distinguishes `sat`, `unsat`, `timeout` and `error`. `witness_checker.py`
recomputes every S-box transition, linear relation, round link and total weight
independently of the encoded SAT cost variables. Solver jobs preserve the CNF,
request, raw output, witness and result metadata below the run's security
directory.

## Result semantics

The unchanged Team A entry point is:

```python
from team_plugins.security_evaluator import evaluate_security
result = evaluate_security(candidate, context)
```

The plugin returns `status="ok"` only when both differential and linear modes
are `completed` and `optimal`. A `timeout`, `bounded` or merely `feasible`
mode returns `status="unavailable"` with `[0.0]` compatibility weights; those
zeros are not security measurements. Invalid input or solver failures return
`status="error"` and no fabricated score. The complete `SecurityReport`, the
Team A fingerprint and the internal B candidate hash are retained in
`artifacts`.

Candidates outside 3--12 rounds are rejected from real SAT analysis before a
solver process is started. The current project smoke defaults are still
`INIT_NUM_ROUNDS=1`, `MAX_NUM_ROUNDS=2`, and population size 1. Consequently a
plain `python main.py` with those defaults will report Team B security as
unavailable for the two-round candidate; this is intentional and is not a
claim that the candidate has zero security. Use the supplied 4-round smoke
fixture or configure the search for at least 3 rounds before interpreting a
Team B score.

The paper profile is separate. Its frozen values (differential
`2, 8, 14, 25`; linear `1, 4, 7, 13`) belong only to the paper's `W(0,r)`
window and are never copied into generated candidate results.

## Files integrated

- `team_plugins/security_evaluator.py`: drop-in API 1.0 entry point;
- `src/llm_cipher/security/`: candidate reader, DDT/Walsh math, CNF model,
  strict solver runner, optimizer, witness checker, report writer, CLI and
  Team A adapter;
- `configs/`: smoke, four-round, multiround and paper-profile configurations;
- `data/fixtures/`: valid/invalid candidates, summary vector and tiny SPN;
- `tests/security/`: unit, contract and (when Kissat is available) real solver
  tests;
- `docs/security_contract_v0.md` and `docs/team_a_plugin_integration.md`:
  detailed field-level contracts.

## Dependencies in this repository

The project runtime currently declares:

```text
json5==0.14.0
numpy==2.0.2
pyosys==0.65
```

The integrated Team B core itself uses only the Python standard library. The
three packages above are still required by the existing Team A generator and
legacy/Yosys path. Python 3.11 or newer is recommended; the repository has no
lock file that pins a patch release, so record the exact `python --version`
used for a reproducibility run.

Install the current repository dependencies from its root:

```bash
python -m venv .venv
# Linux/WSL
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

There is no `pyproject.toml` in this checkout. Direct execution from the
repository root is supported. The plugin entry point adds the local `src`
directory automatically; alternatively set `PYTHONPATH` explicitly:

```bash
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

## SAT solver: Kissat

The Team B implementation uses **Kissat**, not an SMT solver. The exact
executable is selected by `UKNIT_B_SOLVER` or by `context["b_security"]`.
The default command is `kissat`. The code passes one DIMACS CNF path as a
separate argument and parses Kissat's exact `s SATISFIABLE` / `s UNSATISFIABLE`
status lines and exit codes 10/20. It does not use Espresso, Yosys or OpenLane
for Team B security analysis.

The project does not pin a Kissat release. Record both the version output and,
when built from source, the Git commit. The upstream source and build recipe
are maintained at <https://github.com/arminbiere/kissat>; the upstream README
documents `./configure && make test` and the `build/kissat` binary.

### Ubuntu/WSL

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git build-essential
mkdir -p "$HOME/tools"
cd "$HOME/tools"
git clone https://github.com/arminbiere/kissat.git
cd kissat
./configure
make -j"$(nproc)"
./build/kissat --version
export UKNIT_B_SOLVER="$HOME/tools/kissat/build/kissat"
test -x "$UKNIT_B_SOLVER"
"$UKNIT_B_SOLVER" --version
```

For a team run, replace the floating clone with a recorded tag or commit and
save `git -C "$HOME/tools/kissat" rev-parse HEAD` beside the experiment.

### Windows

The security evaluator can run with a native Windows Kissat executable if its
path is supplied, but the reference deployment is WSL2/Ubuntu. In PowerShell,
set the environment variable for the process before starting Python:

```powershell
$env:UKNIT_B_SOLVER = "C:\\tools\\kissat\\build\\kissat.exe"
& $env:UKNIT_B_SOLVER --version
python main.py
```

Do not pass a Linux `/home/...` path to a native Windows Python process. In
WSL, use the Linux path and run the WSL Python interpreter instead.

### Solver-specific environment variables

```text
UKNIT_B_SOLVER                    executable or absolute path; default kissat
UKNIT_B_SINGLE_QUERY_TIMEOUT_S    default 5 seconds
UKNIT_B_TOTAL_TIMEOUT_S_PER_MODE  default 20 seconds
UKNIT_B_MAXIMUM_WEIGHT            default auto, derived from actual tables
```

The same values can be passed under `context["b_security"]` using
`solver_executable`, `single_query_timeout_s`, `total_timeout_s_per_mode` and
`maximum_weight`. The report records the solver version detected by
`kissat --version` when that command is available.

Security reports are stored below `runs/<run-id>/security/` with a short
cache-directory name (`c-<hash-prefix>`), rather than nesting complete
64-character fingerprints and cache digests in the Windows path. Individual
solver jobs likewise use short `h-<hash-prefix>` and `q-<id-prefix>` directory
components. The complete cache key remains in `cache_key.txt`, and each job's
complete candidate hash remains in `request.json`. This preserves cache
identity and evidence while avoiding Windows path-length failures in long
project checkout paths.

## Verification and execution

From the repository root:

```bash
python -c "from team_plugins.security_evaluator import PLUGIN_API_VERSION, evaluate_security; print(PLUGIN_API_VERSION, callable(evaluate_security))"
python -m unittest discover -s tests/security -p 'test_*.py' -v
python -m unittest discover -s tests -p 'test_*.py' -v
python src/llm_cipher/security/candidate_reader.py data/fixtures/candidate_valid.json --expect-summary data/fixtures/candidate_valid_summary.json
```

The first command must print `1.0 True`. The test suite always exercises the
math, parsing, report and state-machine checks. Real Kissat tests are skipped
with an explicit message when the configured fixture path does not exist; they
become active after `KISSAT_PATH` or the test's expected solver path is set.

A direct four-round smoke evaluation is (the `src` package is not installed by
this checkout, so expose it explicitly):

```bash
export UKNIT_B_SOLVER="$HOME/tools/kissat/build/kissat"
PYTHONPATH="$PWD/src" python -m llm_cipher.security.cli \
  --config configs/security_smoke.json \
  --candidate data/fixtures/candidate_valid.json \
  --output runs/security_smoke/security_report.json
```

The CLI exits zero when a report was written, not when optimality was proven;
read every mode's `execution_status` and `conclusion`. The Team A search uses
the same adapter automatically in plugin mode.

## Separation from performance analysis

OpenLane is a Team C performance dependency, not a Team B security dependency.
`config.OPENLANE['ENABLED']` controls only performance analysis. Turning it off
sets the project latency placeholder to `1.0` and does not disable the Kissat
security evaluator. Conversely, enabling Team B security does not require
Docker, WSL, OpenLane or Yosys.
