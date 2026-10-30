# Changelog

## 0.2.0 - 2026-10-31

### Added

- Each edit is shown as a diff and needs approval. `--yes` skips the question.
- `replace_range`, `insert_lines` and `delete_lines` tools for multi-line edits.
- CMake and Make support with automatic detection, plus `--build` and `--test-cmd`.
- Automated verdicts from `--test-cmd`, CTest and `make test`, with a baseline check before any edit.
- OpenRouter, Gemini and Ollama providers, selected with `--provider` and `--model`.
- `patchpilot-bench` and a benchmark of ten seeded bugs with hidden tests.
- `--patch-file` and `--commit` to export or commit a confirmed fix.
- A Streamlit web demo in `webapp/`, with a Bubblewrap sandbox for hosted use.

### Changed

- The run stops after three rejected API requests in a row, where it used to spend every step and attempt.

## 0.1.0

First version: a Groq-powered agent that searched C++ code, replaced single lines, verified each attempt with g++ and restored every file if no attempt succeeded.
