"""Prompt templates for LLM-based concept definition generation."""

from __future__ import annotations


SYSTEM_PROMPT = """\
You are a code analysis assistant specializing in extracting domain concepts from software. \
You will be given a concept (identifier or noun phrase) extracted from a Python codebase, \
along with its surrounding code context. Your task is to define what this concept IS — \
its meaning, role, and domain significance — NOT what the code does with it.

Rules:
1. Output exactly ONE sentence.
2. Focus on the concept's identity and role in the domain.
3. Do NOT describe what the code does or how it is implemented.
4. Do NOT mention "function", "variable", "class", "method", or other programming constructs.
5. Start your definition with the concept name.

Example:
Concept: user in an accounts module
Definition: A user is a person who has registered for an account on the Open Library platform to borrow books and manage reading lists.
"""

USER_TEMPLATE = """\
Concept name: {concept_name}
Raw identifier: {raw_identifier}
File: {file_path}
Line: {line_number}
Enclosing class: {class_name}
Enclosing function: {function_name}
Block type: {block_type}

Surrounding code:
```python
{code_snippet}
```

Define what "{concept_name}" IS in this specific context. \
Output only the definition sentence."""


def build_prompts(
    concept_name: str,
    raw_identifier: str,
    file_path: str,
    line_number: int,
    class_name: str | None,
    function_name: str | None,
    block_type: str,
    code_snippet: str,
) -> tuple[str, str]:
    """Build system and user prompts for a single concept occurrence.

    Returns:
        (system_prompt, user_prompt) tuple.
    """
    user = USER_TEMPLATE.format(
        concept_name=concept_name,
        raw_identifier=raw_identifier,
        file_path=file_path,
        line_number=line_number,
        class_name=class_name or "(none)",
        function_name=function_name or "(none)",
        block_type=block_type,
        code_snippet=code_snippet,
    )
    return SYSTEM_PROMPT, user


# ---------------------------------------------------------------------------
# Synonym judgment prompts (Track B of synonym detection)
# ---------------------------------------------------------------------------

SYNONYM_JUDGE_SYSTEM = """\
You are a code-analysis assistant. You will be given two concepts extracted \
from the same Python codebase, each with its definition and representative \
identifiers. Decide whether the two concepts refer to **the same kind of \
real-world or domain entity** within this codebase's context.

Answer "YES" only if the two concepts are interchangeable: any reference to \
one could plausibly be replaced by a reference to the other without changing \
the meaning of the surrounding code. Otherwise answer "NO".

Be especially careful with cases like book vs. work in a library catalog — \
they are *related* but NOT synonymous because "work" is the abstract creation \
and "book" is the physical artifact.

Reply in exactly two lines:
LINE 1: YES or NO
LINE 2: One short sentence justifying your answer.
"""

SYNONYM_JUDGE_USER_TEMPLATE = """\
Concept A: {name_a}
  Identifiers: {ids_a}
  Definition: {def_a}

Concept B: {name_b}
  Identifiers: {ids_b}
  Definition: {def_b}

Are A and B synonyms in this codebase? Answer YES or NO with a one-sentence reason.
"""


def build_synonym_judge_prompts(
    name_a: str,
    ids_a: list[str],
    def_a: str,
    name_b: str,
    ids_b: list[str],
    def_b: str,
) -> tuple[str, str]:
    """Build prompts for asking the LLM to judge if two concepts are synonyms."""
    user = SYNONYM_JUDGE_USER_TEMPLATE.format(
        name_a=name_a,
        ids_a=", ".join(ids_a[:5]) or "(none)",
        def_a=def_a or "(no definition available)",
        name_b=name_b,
        ids_b=", ".join(ids_b[:5]) or "(none)",
        def_b=def_b or "(no definition available)",
    )
    return SYNONYM_JUDGE_SYSTEM, user


def parse_synonym_judgment(response: str) -> tuple[bool, str]:
    """Parse a YES/NO + reason response from the LLM.

    Returns (is_synonym, reason). Defaults to (False, raw_response) on parse
    failure — we'd rather drop a candidate than wrongly assert synonymy.
    """
    if not response:
        return False, ""
    text = response.strip()
    first_line = text.splitlines()[0].strip().upper() if text else ""
    is_syn = first_line.startswith("YES")
    # Reason is whatever comes after; fall back to the full text.
    lines = text.splitlines()
    reason = lines[1].strip() if len(lines) >= 2 else text
    return is_syn, reason
