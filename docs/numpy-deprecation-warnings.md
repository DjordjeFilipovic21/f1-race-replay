# Historical NumPy deprecation-warning evidence

**Record date:** 2026-08-15

**Scope:** This is a historical warning-discovery and remediation record for
the modern contract/pipeline tests and the preserved legacy test suite. The
underlying discovery runs predate this documentation review; their execution
date was not captured. The local runtime reported by those runs was Python
3.14.6.

## Observed evidence

The historical discovery run forced `DeprecationWarning` visibility:

```bash
.venv/bin/python -W always::DeprecationWarning -m pytest tests/contracts pipeline/tests
.venv/bin/python -W always::DeprecationWarning -m pytest legacy/tests
```

Recorded results were:

- Modern contract and pipeline suite: **2192 passed, 2 warnings**.
- Legacy suite: **94 passed, 0 warnings**.

The modern count was a baseline from before the reproducibility tests in
`pipeline/tests/reproducibility/test_dependency_constraints.py`; those four
tests were not included in the baseline count.

Both modern warnings came from test-input construction in
`pipeline/tests/adapters/fastf1/test_messages_results.py`, where
`pandas.Timedelta("1.2345s")` and `pandas.Timedelta("1.0005s")` exercised
NumPy's deprecated generic timedelta unit. No warning originated in
`pipeline/src` or `legacy/src`, and static inspection found no deprecated NumPy
aliases at the project boundary.

## Remediation status

The repository records the focused remediation as complete:

- Test inputs use standard-library `datetime.timedelta` microseconds.
- The behavior assertion is protected by a focused
  `error::DeprecationWarning` marker.
- No global `filterwarnings` rule was added to `pytest.ini`, and no third-party
  warning was blanket-suppressed.

The focused command recorded for the boundary is:

```bash
.venv/bin/python -m pytest pipeline/tests/adapters/fastf1/test_messages_results.py::test_adapt_results_converts_qualifying_times_and_preserves_missing_segments -W error::DeprecationWarning
```

## Historical post-remediation result

A later historical update recorded **2197 passed, 0 warnings** for the modern
contract/pipeline suite and **94 passed, 0 warnings** for the legacy suite. The
modern arithmetic was documented as 2192 baseline tests + 4 reproducibility
tests + 1 constraints-alignment test. These counts are recorded evidence from
that update, not a claim that a full warning-discovery run was performed during
the 2026-08-15 documentation review.

The current remediation status should be rechecked with the focused command or
the warning-discovery commands after dependency, test-input, or Python-version
changes.
