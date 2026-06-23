"""Noun/noun-phrase filtering for extracted identifier tokens.

This module is the gatekeeper between "every Python identifier" and
"openlibrary domain concept candidates". It applies three layers:

  1. Static blocklists (stop words, stdlib modules, builtin methods,
     framework symbols of web.py / infogami / pytest).
  2. Min-length and pure-digit filters.
  3. Domain-abbreviation preservation (ISBN, OLID, ...).

A second, *dynamic* coverage-based filter (drop tokens that appear in
>50% of files) is applied **at the corpus level** by extraction code
that has visibility into all files — see `filter_by_coverage()`.
"""

from __future__ import annotations


DEFAULT_STOP_WORDS: set[str] = {
    # Pronouns / sentinels
    "self", "cls", "args", "kwargs", "tmp", "temp", "item", "value",
    "result", "data", "info", "ctx", "req", "res", "obj", "ptr",
    "key", "val", "ret", "err", "msg", "idx", "num", "cnt",
    "src", "dst", "buf",
    # Python type names (often used as parameter names too)
    "len", "str", "int", "float", "bool",
    "list", "dict", "set", "tuple", "type", "none", "true", "false",
    # Python keywords (defensive — AST shouldn't yield these, but split_identifier might)
    "def", "class", "return", "yield", "import", "from", "if",
    "else", "elif", "for", "while", "try", "except", "with",
    "as", "in", "not", "and", "or", "is", "pass", "raise",
    "break", "continue", "global", "nonlocal", "lambda",
}


# Python builtin functions and types we never want as concepts.
PYTHON_BUILTINS: set[str] = {
    "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
    "callable", "chr", "classmethod", "compile", "complex", "delattr",
    "dict", "dir", "divmod", "enumerate", "eval", "exec", "exit",
    "filter", "float", "format", "frozenset", "getattr", "globals",
    "hasattr", "hash", "help", "hex", "id", "input", "int", "isinstance",
    "issubclass", "iter", "len", "list", "locals", "map", "max", "memoryview",
    "min", "next", "object", "oct", "open", "ord", "pow", "print",
    "property", "quit", "range", "repr", "reversed", "round", "set",
    "setattr", "slice", "sorted", "staticmethod", "str", "sum", "super",
    "tuple", "type", "vars", "zip",
    # Special names
    "main", "init", "name", "doc", "file", "module", "class_",
    "type_checking",
}


# Methods on Python's str / list / dict / set / tuple / file / general protocol.
# These appear as identifiers via attribute access (`x.append`, `s.split`) and
# carry no openlibrary-domain meaning.
PYTHON_BUILTIN_METHODS: set[str] = {
    # str methods
    "split", "rsplit", "splitlines", "strip", "rstrip", "lstrip",
    "join", "lower", "upper", "title", "capitalize", "swapcase",
    "casefold", "startswith", "endswith", "find", "rfind", "index",
    "rindex", "replace", "translate", "maketrans", "format",
    "format_map", "encode", "decode", "isalpha", "isdigit",
    "isalnum", "isspace", "isupper", "islower", "istitle",
    "isnumeric", "isdecimal", "isidentifier", "isprintable", "isascii",
    "center", "ljust", "rjust", "zfill", "expandtabs", "partition",
    "rpartition", "count", "removeprefix", "removesuffix",
    # list methods
    "append", "extend", "insert", "remove", "pop", "clear", "copy",
    "reverse", "sort",
    # dict methods
    "keys", "values", "items", "get", "setdefault", "update", "popitem",
    "fromkeys",
    # set methods
    "add", "discard", "union", "intersection", "difference",
    "symmetric_difference", "issubset", "issuperset", "isdisjoint",
    # file/IO methods
    "read", "readline", "readlines", "write", "writelines", "seek",
    "tell", "flush", "close", "readable", "writable", "seekable",
    "fileno",
    # general dunder-stripped
    "init", "new", "del", "repr", "str", "bytes", "hash", "eq", "ne",
    "lt", "le", "gt", "ge", "bool", "len", "iter", "next", "call",
    "enter", "exit", "getitem", "setitem", "delitem", "contains",
    "add_", "sub_", "mul_", "div_",
    # JSON
    "dumps", "loads", "dump", "load",
    # threading/asyncio common
    "acquire", "release", "wait", "notify", "run", "start", "join",
    "cancel", "result", "set_result",
}


# Standard-library module names. They appear as `import X` identifiers.
PYTHON_STDLIB_MODULES: set[str] = {
    "os", "sys", "re", "json", "datetime", "time", "math", "random",
    "logging", "logger", "functools", "itertools", "collections",
    "typing", "pathlib", "subprocess", "threading", "asyncio",
    "concurrent", "multiprocessing", "queue", "socket", "http",
    "urllib", "ssl", "hashlib", "hmac", "base64", "binascii", "uuid",
    "io", "csv", "xml", "html", "email", "smtplib", "imaplib", "ftplib",
    "ast", "inspect", "traceback", "warnings", "weakref", "copy",
    "pickle", "shelve", "sqlite", "sqlite3", "shutil", "tempfile",
    "glob", "fnmatch", "argparse", "configparser", "getpass", "getopt",
    "platform", "atexit", "signal", "errno", "gc", "ctypes", "struct",
    "array", "enum", "abc", "contextlib", "dataclasses",
    "operator", "string", "textwrap", "unicodedata", "codecs",
    "decimal", "fractions", "statistics", "secrets", "zlib", "gzip",
    "tarfile", "zipfile",
    # Common third-party stubs we don't want as domain concepts
    "pytest", "unittest", "mock", "doctest", "pdb", "ipdb",
    "requests", "httpx", "aiohttp",
    "numpy", "pandas", "yaml",
    "click",
}


# Symbols from web.py and infogami that are framework plumbing, not
# openlibrary domain concepts. (openlibrary is built on web.py + infogami.)
FRAMEWORK_SYMBOLS: set[str] = {
    # web.py
    "web", "ctx", "input", "header", "cookies", "setcookie", "seeother",
    "notfound", "internalerror", "badrequest", "unauthorized",
    "forbidden", "found", "tempredirect", "redirect", "storage",
    "storify", "websafe", "form", "renderer", "render", "renderer_",
    "application", "subapp", "loadhook", "unloadhook", "delegate",
    "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD",
    # infogami
    "infogami", "client", "config", "core", "plugin", "macro",
    "view", "public", "thingdict", "thingref",
    "render_template", "template", "templates",
    # pytest / typing internals leaking into AST
    "fixture", "mark", "parametrize", "monkeypatch", "tmp_path",
    "tmpdir", "capsys", "capfd", "caplog",
    "any", "literal", "cast", "overload", "final", "protocol",
    "typevar", "generic", "iterable", "iterator", "callable",
    "mapping", "sequence", "optional", "union", "tuple_", "dict_",
}


# Verbs commonly seen as function-name prefixes. These tokens are useful
# at the **identifier** level (we still want to record `get_user_email`
# as an occurrence) but should NOT become standalone concepts on their own.
COMMON_VERB_TOKENS: set[str] = {
    "get", "set", "find", "create", "update", "delete", "remove",
    "process", "handle", "fetch", "load", "save", "compute",
    "validate", "check", "run", "build", "parse", "format",
    "convert", "transform", "encode", "decode", "read", "write",
    "add", "make", "do", "send", "receive", "start", "stop",
    "open", "close", "init", "clean", "sort", "filter", "map",
    "render", "display", "show", "hide", "enable", "disable",
    "generate", "register", "unregister", "install", "uninstall",
    "configure", "setup", "reset", "clear", "push", "pop",
    "insert", "append", "extend", "test", "assert", "log",
}


# Combined hard-block set: anything in here is dropped at the *token* level
# during identifier splitting (configured in name_splitter.split_name_filter_nouns).
HARD_BLOCKLIST: set[str] = (
    DEFAULT_STOP_WORDS
    | PYTHON_BUILTINS
    | PYTHON_BUILTIN_METHODS
    | PYTHON_STDLIB_MODULES
    | FRAMEWORK_SYMBOLS
)


def is_noun_like(token: str) -> bool:
    """Heuristic check: is this token likely a noun?

    Verbs are filtered separately because callers may want to keep them
    for compound identifiers (e.g. "get_user" should still record "user"
    even if "get" is a verb).
    """
    return token.lower() not in COMMON_VERB_TOKENS


def filter_tokens(
    tokens: list[str],
    stop_words: set[str] | None = None,
    keep_abbreviations: set[str] | None = None,
    min_length: int = 2,
) -> list[str]:
    """Filter tokens through the hard blocklist + length/digit rules.

    This is the per-token gate. Corpus-level filtering (e.g. "appears in
    >50% of files") is handled separately by `filter_by_coverage()`.
    """
    if stop_words is None:
        stop_words = HARD_BLOCKLIST
    keep_abbreviations = keep_abbreviations or set()

    result: list[str] = []
    for t in tokens:
        lower = t.lower()
        if len(t) < min_length:
            continue
        if lower in stop_words:
            continue
        if t.isdigit():
            continue
        if t.upper() in keep_abbreviations:
            result.append(t.upper())
        else:
            result.append(lower)
    return result


def filter_by_coverage(
    concept_to_files: dict[str, set[str]],
    total_files: int,
    max_file_ratio: float = 0.5,
) -> set[str]:
    """Identify concepts that appear in too many files to carry domain meaning.

    A concept that shows up in >50% of files in the codebase is almost
    certainly framework plumbing (e.g. `logger`, `config`) rather than a
    domain concept. Returns the set of concept names to DROP.

    Args:
        concept_to_files: mapping concept-name -> set of file paths it appears in.
        total_files: total number of files parsed in this pass.
        max_file_ratio: drop any concept whose len(files) / total_files
            strictly exceeds this ratio.
    """
    if total_files <= 0:
        return set()
    cutoff = max_file_ratio * total_files
    return {
        name for name, files in concept_to_files.items()
        if len(files) > cutoff
    }
