# Team B/C Plugin Contract

This directory is the stable boundary between the Team A genetic-search
orchestrator and the Team B/C evaluators. The framework passes JSON-compatible
dictionaries, never internal Member objects. Replace the two placeholder files
while preserving the exact public entry points.

## Required files and entry points

Team B replaces security_evaluator.py:

    PLUGIN_API_VERSION = "1.0"

    def evaluate_security(candidate: dict, context: dict) -> dict:
        ...

Team C replaces engineering_evaluator.py:

    PLUGIN_API_VERSION = "1.0"

    def validate_candidate(candidate: dict, context: dict) -> dict:
        ...

    def evaluate_performance(candidate: dict, context: dict) -> dict:
        ...

PLUGIN_NAME is optional but recommended for logs. Keep
PLUGIN_API_VERSION exactly "1.0". The default loader imports these files as
team_plugins.security_evaluator and team_plugins.engineering_evaluator; no
change to main.py is needed.

## Candidate input

Every entry point receives a JSON-compatible dictionary with this stable
top-level shape:

    {
      "schema_version": "1.0",
      "candidate_id": "r03-g0000-p0001",
      "fingerprint": "SHA-256 of the normalized cipher structure",
      "num_rounds": 3,
      "rounds": [
        {
          "round_index": 0,
          "sboxes": "16 S-box permutations, each containing 0..15",
          "linear_matrix": "64x64 invertible binary matrix, or null in final round"
        }
      ],
      "metadata": {}
    }

The example describes field types, not a literal valid candidate. A real
candidate has exactly 16 S-boxes per round, each a permutation of 0..15.
Every non-final round has an invertible 64 by 64 binary linear_matrix; the
final round has linear_matrix set to null. Do not mutate the input dictionary.
Use candidate_id unchanged in every result, and use fingerprint to correlate
diagnostics with the analyzed structure.

context is a JSON-compatible dictionary of optional orchestration metadata. It
may contain run_id, generation, num_rounds, population_index, and population
summaries. Treat all keys as optional and do not require framework-private
objects.

## Common result fields

All three functions return a JSON-compatible dictionary. The loader normalizes
the result, but implementations should return these fields directly:

    {
      "schema_version": "1.0",
      "plugin_api_version": "1.0",
      "plugin_name": "team-b-security",
      "candidate_id": "same id as input",
      "status": "ok",
      "warnings": [],
      "errors": [],
      "artifacts": {}
    }

status must be one of ok, unavailable, or error. Use unavailable when an
external tool or data source is absent and error when the candidate cannot be
evaluated. Do not return fabricated measurements. In non-strict framework mode,
exceptions and invalid responses are converted to unavailable so the search can
continue with neutral fitness.

## Team B: security result

Add these fields to the common result:

    "differential": {"weights": [0.0], "trails": []},
    "linear": {"weights": [0.0], "trails": []}

weights must be finite numeric values and trails must be a JSON list. Detailed
diagnostics can be placed in artifacts. A status other than ok is treated as
unavailable or invalid and does not contribute a security score.

## Team C: validation result

Add the field valid, which must be boolean. Set valid to false and put
structured diagnostics in errors if the candidate should not be accepted.
Team A invokes this validator during normal evaluation and after an LLM
mutation. It must be deterministic for identical input.

## Team C: performance result

Add valid and a metrics object:

    {
      "valid": true,
      "metrics": {
        "latency": 12.5,
        "area": null,
        "energy": null,
        "units": {"latency": "ns"}
      }
    }

latency, area, and energy must be finite numbers or null; metrics and units
must be dictionaries. Only an ok validation result, an ok security result, and
an ok performance result permit non-neutral fitness.

## Placeholder and no-op behavior

The committed placeholder files deliberately return status=unavailable for
Team B security and Team C performance. Team C validation performs shared
structural checks and warns that full engineering checks are absent. This lets
the genetic architecture, crossover, logging, and plugin integration run
without claiming scientific results.

Plugin results, exceptions, and contract failures are logged with status and
diagnostics. Keep result payloads JSON-compatible and avoid secrets in warnings,
errors, or artifacts.
