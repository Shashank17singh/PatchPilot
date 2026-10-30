"""Streamlit web demo. Run locally with: streamlit run webapp/app.py"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Community Cloud installs this folder's requirements.txt, not the package itself.
REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "src"))

from patchpilot import web
from patchpilot.config import get_provider
from patchpilot.errors import PatchPilotError
from patchpilot.sandbox import sandbox_available

DEMO_FILE = REPOSITORY / "examples" / "cpp_demo" / "main.cpp"
DEMO_BUG = "add(2, 3) returns -1 but it should return 5."
SOURCES = ("Demo bug", "Paste code", "Upload files")


@st.cache_resource
def has_sandbox() -> bool:
    return sandbox_available()


def demo_code() -> str:
    return DEMO_FILE.read_text(encoding="utf-8").replace("return a + b;", "return a - b;")


def collect_files(source: str) -> dict[str, str]:
    if source == "Demo bug":
        return {"main.cpp": st.text_area("Code with a seeded bug", demo_code(), height=300)}
    if source == "Paste code":
        code = st.text_area("C++ code", height=300, placeholder="Paste a single-file program")
        return {"main.cpp": code} if code.strip() else {}

    uploads = st.file_uploader(
        "C and C++ files",
        type=[extension.lstrip(".") for extension in sorted(web.CODE_EXTENSIONS)],
        accept_multiple_files=True,
    )
    return {upload.name: upload.getvalue().decode("utf-8", "replace") for upload in uploads or []}


def show_outcome(outcome: web.FixOutcome) -> None:
    if not outcome.solved:
        st.error("PatchPilot could not fix this bug within the demo's limits.")
    elif outcome.executed:
        st.success("Fix found. It compiled and ran inside the sandbox.")
    else:
        st.warning("Fix proposed, but it was not compiled or run. Review the diff carefully.")

    if outcome.diff:
        st.subheader("Proposed change")
        st.code(outcome.diff, language="diff")
        st.download_button("Download patch", outcome.diff, "patchpilot-fix.patch")
    elif outcome.solved:
        st.info("The model made no changes.")

    st.caption(f"LLM calls: {outcome.llm_calls}   tokens used: {outcome.tokens}")
    with st.expander("Full agent log"):
        st.code(outcome.log, language="text")


st.set_page_config(page_title="PatchPilot", page_icon="🛠️")
st.title("PatchPilot")
st.write("Describe a C++ bug in plain English. An LLM agent finds it and proposes a fix.")

with st.sidebar:
    st.header("Model")
    provider = st.selectbox("Provider", web.WEB_PROVIDERS)
    model = st.text_input("Model", get_provider(provider).default_model)
    api_key = st.text_input(
        f"{provider.capitalize()} API key",
        type="password",
        help="Used for this session only and never stored.",
    )
    st.header("Safety")
    if has_sandbox():
        st.success("Sandbox active: code runs isolated, with no network.")
    else:
        st.warning("No sandbox on this server: code is edited but never compiled or run.")

files = collect_files(st.radio("Code", SOURCES, horizontal=True))
bug = st.text_area("What is wrong?", DEMO_BUG)
expected = st.text_area(
    "Expected program output (optional)",
    help="When set, the agent keeps trying until the output matches. Needs the sandbox.",
)

runs = st.session_state.setdefault("runs", 0)
limit_reached = runs >= web.MAX_RUNS_PER_SESSION
if limit_reached:
    st.info(f"This demo allows {web.MAX_RUNS_PER_SESSION} runs per session.")

if st.button("Fix it", type="primary", disabled=limit_reached):
    st.session_state["outcome"] = None
    try:
        settings = web.web_settings(provider, model, api_key)
        st.session_state["runs"] = runs + 1
        with st.status("Working on the bug...", expanded=True) as status:
            log = st.empty()
            lines: list[str] = []

            def show_line(line: str) -> None:
                lines.append(line)
                log.code("\n".join(lines[-20:]), language="text")

            st.session_state["outcome"] = web.run_fix(
                files, bug, settings, expected_output=expected, on_line=show_line
            )
            status.update(label="Finished", state="complete", expanded=False)
    except PatchPilotError as exc:
        st.error(str(exc))

if st.session_state.get("outcome"):
    show_outcome(st.session_state["outcome"])
