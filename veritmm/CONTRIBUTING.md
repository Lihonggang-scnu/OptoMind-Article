# Contributing

Contributions are welcome, especially independent validation cases, material
provenance improvements, numerical-stability fixes, and reproducible examples.

1. Fork the repository and create a focused branch.
2. Install with `python -m pip install -e ".[test]"`.
3. Add or update tests for every physics or contract change.
4. Run `python -m pytest -q` before opening a pull request.
5. Explain numerical conventions, units, validity limits, and data provenance.

Do not hide non-finite values, clip raw spectra to make a test pass, silently
extrapolate optical constants, or weaken an acceptance check without a
scientific justification and regression case.


## Releases and DOI

Releases are tagged on GitHub; the Zenodo GitHub integration archives every
release and mints a versioned DOI (one-time setup: link the repository at
zenodo.org and enable the VeriTMM concept DOI).  `CITATION.cff` carries the
release metadata and is kept in lockstep with `tmm_engine/_version.py` and
`pyproject.toml` by the `version-identity` CI gate — update all three (or
only the runtime source) when bumping the version.  Cite the software via
the CITATION.cff content and the original optical-constant datasets selected
by your runs.
