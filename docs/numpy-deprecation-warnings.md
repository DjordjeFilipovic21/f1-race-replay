# NumPy deprecation warning evidence

## Baseline

The warning discovery run used the project virtual environment and forced
`DeprecationWarning` visibility:

```bash
.venv/bin/python -W always::DeprecationWarning -m pytest tests/contracts pipeline/tests
.venv/bin/python -W always::DeprecationWarning -m pytest legacy/tests
```

The local environment was Python 3.14.6. Results:

- Modern contract and pipeline suite: **2192 passed, 2 warnings**.
- Legacy suite: **94 passed, 0 warnings**.

Both modern warnings came from the same test input construction in
`pipeline/tests/adapters/fastf1/test_messages_results.py`, where
`pandas.Timedelta("1.2345s")` and `pandas.Timedelta("1.0005s")` exercised
NumPy's deprecated generic timedelta unit. No warning originated in
`pipeline/src` or `legacy/src`, and static inspection found no deprecated NumPy
aliases in project-boundary code.

## Remediation

The test inputs now use explicit standard-library `datetime.timedelta`
microseconds. The behavior assertion is protected by a focused
`error::DeprecationWarning` marker, so a regression in this boundary fails the
test rather than being silently accepted.

No global `filterwarnings` rule was added to `pytest.ini`, and no third-party
warning was blanket-suppressed. The warning-free focused check is:

```bash
.venv/bin/python -m pytest pipeline/tests/adapters/fastf1/test_messages_results.py::test_adapt_results_converts_qualifying_times_and_preserves_missing_segments -W error::DeprecationWarning
```

The dependency constraints are resolved against Python 3.11 as the lowest
supported CI version; the warning baseline above documents the available local
runtime separately.

## Post-remediation results

After the remediation, the same warning-discovery runs report **zero**
`DeprecationWarning`s from NumPy or any other source:

- Modern contract and pipeline suite: **2192 passed, 0 warnings**.
- Legacy suite: **94 passed, 0 warnings**.

The test counts match the baseline (2192 modern, 94 legacy): the focused
boundary fix removed the two warnings without changing test coverage, and no
blanket suppression was introduced.
