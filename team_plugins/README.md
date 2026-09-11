# Team B/C Plugin Contract

This directory is the stable boundary between the Team A search orchestrator
and the Team B/C evaluators. The framework passes JSON-compatible dictionaries,
not internal `Member` objects.

## Public entry points

Team B is implemented in `security_evaluator.py` and exports:

```python
PLUGIN_API_VERSION = "1.0"
evaluate_security(candidate, context)
evaluate(candidate, context)
```

The implementation is a thin drop-in adapter over
`src/llm_cipher/security/team_a_adapter.py`. It converts Team A's dense
candidate representation to the Team B CandidateSpec, runs the strict Kissat
analysis, and maps the audited report back to the Team A result shape.

Team C exports `validate_candidate(candidate, context)` and
`evaluate_performance(candidate, context)` from `engineering_evaluator.py`.
Keep `PLUGIN_API_VERSION` exactly `"1.0"` for both teams.

## Candidate input

The Team A-side candidate has this shape:

```json
{
  "schema_version": "1.0",
  "candidate_id": "r04-g0000-p0000",
  "fingerprint": "SHA-256 of the normalized cipher structure",
  "num_rounds": 4,
  "rounds": [
    {
      "round_index": 0,
      "sboxes": "16 permutations of 0..15",
      "linear_matrix": "64x64 binary matrix, or null in the final round"
    }
  ],
  "metadata": {}
}
```

The security adapter independently checks the converted CandidateSpec: MSB0
bit numbering, MANTIS S-box design space, GF(2)-invertible linear layers and
semantic candidate hash. It does not silently repair malformed candidates.

## Security result semantics

The result always contains the common API fields plus `differential` and
`linear` metric groups. Team B returns `status="ok"` only if both modes are
`completed + optimal` after independent witness validation. A timeout,
bounded result or feasible-only result is `status="unavailable"`; its `[0.0]`
weights are compatibility placeholders, not measurements. Invalid input or a
solver error is `status="error"` and carries structured diagnostics.

Candidates outside 3--12 rounds return `unavailable` before Kissat starts.
The full `SecurityReport`, Team A fingerprint, B candidate hash, solver
version and evidence paths are preserved under `artifacts`.

## Performance and validation result semantics

Team C validation returns `valid: true/false` and structured `errors`.
Performance returns `valid` and:

```json
{
  "metrics": {
    "latency": 12.5,
    "area": null,
    "energy": null,
    "units": {"latency": "ns"}
  }
}
```

Only an accepted validation, an `ok` security result and an `ok` performance
result permit non-neutral fitness. OpenLane can be disabled through the
project performance switch; that changes only Team C latency behavior and does
not disable Team B Kissat analysis.

See `docs/security_analysis_and_environment.md` and
`docs/security_contract_v0.md` for the complete threat model, field-level
schema, solver setup and report semantics.
