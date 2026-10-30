# Contributing

## Setup

```bash
git clone https://github.com/Shashank17singh/PatchPilot.git
cd PatchPilot
make install
```

Tests use a scripted model, so you need neither an API key nor network access. Tests that need `g++`, `make`, `cmake` or a working Bubblewrap are skipped when those are missing. CI has all of them, and it fails if the sandbox tests cannot run.

## Making a change

1. Create a branch from `main`.
2. Add or update a test with your change. `tests/conftest.py` has the scripted model, UI and verifier that most tests use.
3. Run `make all`, which formats the code, checks types and runs the tests.
4. Open a pull request that says what changed and why.

To run the checks on every commit, install the hooks with `pre-commit install`.

## Adding a benchmark case

Each case lives in `benchmarks/cases/<id>/` and needs three things:

- `project/` holds the code containing the bug.
- `case.json` holds the title, the kind of bug, the report the model receives and the reference fix.
- `hidden_test.cpp` decides success. Include `check.h`, rename the project's `main` and exit non-zero on failure. The existing cases show the pattern.

`tests/test_benchmark.py` picks up new cases automatically and checks that the bug fails its hidden test and that the reference fix passes. Update the expected case count in that file too.

## Adding an LLM provider

If the provider has an OpenAI-compatible endpoint, add an entry to `PROVIDERS` in `src/patchpilot/config.py` and a test in `tests/test_llm.py`. If it attaches extra data to tool calls or replies, make sure `_parse_reply` in `src/patchpilot/llm.py` keeps that data so the agent can send it back.

## Reporting bugs

Open an issue with the command you ran, the output and your operating system. For a bug in the agent's behaviour, the model name and the output of a run with `PATCHPILOT_LOG_LEVEL=DEBUG` help most. Remove anything private first, including API keys and source code you cannot share.
