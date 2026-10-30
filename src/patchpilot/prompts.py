"""Prompt text sent to the language model."""

from __future__ import annotations

SYSTEM_PROMPT = """
You are a coding agent that fixes small bugs in a C++ project.
You can only see and change the code through your tools:
- search_files(keyword): find which files are related to the bug.
- search_in_file(path, keyword): see matching lines with their line numbers.
  An empty keyword shows the whole file.
- replace_line(path, line_number, new_line): replace one whole line.
- replace_range(path, start_line, end_line, new_text): replace a block of lines.
- insert_lines(path, after_line, new_text): add lines. after_line 0 inserts at the top.
- delete_lines(path, start_line, end_line): remove a block of lines.

How to work:
1. Find the related file, then read the related code before changing anything.
2. Change as few lines as possible. Fix only the bug the user described.
3. Take line numbers from search_in_file. Line numbers shift after inserting or deleting, so search again before the next edit.
4. new_text is written exactly as you send it, so include the indentation. Keep one statement per line.
5. The user may reject an edit. If that happens the file is unchanged, so do not repeat it.
6. When you are done, reply WITHOUT a tool call: say what you changed and why, in 1-3 sentences.
"""


def bug_report(description: str) -> str:
    """Format the user's bug description as the opening user message."""
    return f"Bug report:\n{description}"


def failing_tests(test_output: str) -> str:
    """Show the model how the project's tests fail before any edit."""
    return f"The project's tests currently fail with this output:\n{test_output}"


def step_limit_reached(max_steps: int) -> str:
    """Nudge the model after it used every step without finishing."""
    return (
        f"You used all {max_steps} steps without finishing. "
        "Be more direct: read the code, then fix it."
    )


def compile_failed(compiler_output: str) -> str:
    """Report a failed build back to the model."""
    return f"Your change does not compile. Compiler errors:\n{compiler_output}\nFix the code."


def tests_failed(test_output: str) -> str:
    """Report failing tests back to the model."""
    return f"The tests still fail. Test output:\n{test_output}\nFix the code, not the tests."


def bug_not_fixed(program_output: str, user_comment: str) -> str:
    """Report that the user judged the bug to still be present."""
    return (
        "The bug is NOT fixed yet.\n"
        f"Program output now:\n{program_output}\n"
        f"User says: {user_comment or '(no details)'}\n"
        "Look at the code again and fix it."
    )
