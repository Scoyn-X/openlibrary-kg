"""Python AST-based identifier extraction.

Walks the AST of each Python file, extracting identifiers from
functions, classes, variables, parameters, imports, etc.
"""

from __future__ import annotations

import ast
from pathlib import Path

from openlibrary_kg.models import CodeContext, ConceptOccurrence


class _IdentifierVisitor(ast.NodeVisitor):
    """AST visitor that extracts identifiers and their contexts."""

    def __init__(self, file_path: str, source_lines: list[str], context_lines: int = 3):
        self.file_path = file_path
        self.source_lines = source_lines
        self.context_lines = context_lines
        self.occurrences: list[ConceptOccurrence] = []
        self._scope_stack: list[tuple[str | None, str | None]] = [
            (None, None)  # (class_name, function_name)
        ]

    @property
    def _scope(self) -> tuple[str | None, str | None]:
        return self._scope_stack[-1]

    def _code_snippet(self, lineno: int, end_lineno: int | None = None) -> str:
        """Extract surrounding lines as a code snippet."""
        n = self.context_lines
        start = max(0, lineno - 1 - n)
        end = min(len(self.source_lines), (end_lineno or lineno) + n)
        lines = self.source_lines[start:end]
        return "".join(lines).rstrip()

    def _block_type(self, node: ast.AST) -> str:
        """Determine block type from the current scope."""
        cls_name, func_name = self._scope
        if func_name and cls_name:
            # Check if it's a method (function inside class)
            return "method"
        if func_name:
            return "function"
        if cls_name:
            return "class"
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return "function" if not cls_name else "method"
        if isinstance(node, ast.ClassDef):
            return "class"
        return "module"

    def _add_occurrence(
        self,
        name: str,
        identifier_type: str,
        lineno: int,
        block_type: str,
        end_lineno: int | None = None,
    ) -> None:
        """Record one identifier occurrence if it passes basic checks."""
        if not name or not name.isascii():
            return
        class_name, func_name = self._scope
        ctx = CodeContext(
            file_path=self.file_path,
            function_name=func_name,
            class_name=class_name,
            line_number=lineno,
            code_snippet=self._code_snippet(lineno, end_lineno),
            block_type=block_type,
        )
        self.occurrences.append(ConceptOccurrence(
            raw_identifier=name,
            split_name="",  # filled in by name_splitter later
            identifier_type=identifier_type,
            context=ctx,
        ))

    # ---- visitors ----

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        cls, func = self._scope
        self._scope_stack.append((cls, node.name))
        self._add_occurrence(node.name, "function_name", node.lineno, self._block_type(node), node.end_lineno)
        # parameters
        for arg in node.args.args:
            self._add_occurrence(arg.arg, "parameter", arg.lineno, self._block_type(node))
        for arg in node.args.posonlyargs:
            self._add_occurrence(arg.arg, "parameter", arg.lineno, self._block_type(node))
        for arg in node.args.kwonlyargs:
            self._add_occurrence(arg.arg, "parameter", arg.lineno, self._block_type(node))
        if node.args.vararg:
            self._add_occurrence(node.args.vararg.arg, "parameter", node.args.vararg.lineno, self._block_type(node))
        if node.args.kwarg:
            self._add_occurrence(node.args.kwarg.arg, "parameter", node.args.kwarg.lineno, self._block_type(node))
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_occurrence(node.name, "class_name", node.lineno, self._block_type(node), node.end_lineno)
        # base classes
        for base in node.bases:
            if isinstance(base, ast.Name):
                self._add_occurrence(base.id, "class_name", base.lineno, self._block_type(node))
            elif isinstance(base, ast.Attribute):
                self._add_occurrence(base.attr, "class_name", base.lineno, self._block_type(node))
        self._scope_stack.append((node.name, None))
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            for name in _extract_target_names(target):
                self._add_occurrence(name, "variable", target.lineno, self._block_type(node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.target:
            for name in _extract_target_names(node.target):
                self._add_occurrence(name, "variable", node.target.lineno, self._block_type(node))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self._add_occurrence(name, "import", node.lineno, self._block_type(node))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            name = alias.asname or alias.name
            self._add_occurrence(name, "import", node.lineno, self._block_type(node))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self._add_occurrence(node.attr, "attribute", node.lineno, self._block_type(node))
        self.generic_visit(node)


def _extract_target_names(target: ast.expr) -> list[str]:
    """Extract variable names from an assignment target."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple) or isinstance(target, ast.List):
        names: list[str] = []
        for elt in target.elts:
            names.extend(_extract_target_names(elt))
        return names
    if isinstance(target, ast.Starred):
        return _extract_target_names(target.value)
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, ast.Subscript):
        return _extract_target_names(target.value)
    return []


def parse_file(
    filepath: str | Path,
    context_lines: int = 3,
) -> list[ConceptOccurrence]:
    """Parse a single Python file and extract all identifier occurrences.

    Args:
        filepath: Absolute or relative path to the Python file.
        context_lines: Number of surrounding lines to include in snippets.

    Returns:
        List of ConceptOccurrence objects (without split_name or definition).
    """
    path = Path(filepath)
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        import logging
        logging.getLogger("openlibrary_kg").warning(
            "Cannot read %s: %s", path, exc
        )
        return []

    source_lines = source.splitlines(keepends=True)

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        import logging
        logging.getLogger("openlibrary_kg").warning(
            "Syntax error in %s: %s", path, exc
        )
        return []

    rel_path = path.as_posix()
    visitor = _IdentifierVisitor(rel_path, source_lines, context_lines)
    visitor.visit(tree)
    return visitor.occurrences
