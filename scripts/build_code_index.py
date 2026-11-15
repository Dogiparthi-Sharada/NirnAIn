"""Builds INDEX.txt, the code-browsing map of the NirnAIn repository.

Purpose: Answer "where is the code for X, and what does it do?" quickly, e.g. in a review.
How it fits: Run from the repository root with `python3.12 scripts/build_code_index.py`. Part 1
is the curated map in `code_index_features.py`; every entry is checked to exist, so it can't go
stale silently. Part 2 is generated from the code itself: every source file with its one-line
summary, and its classes and functions with their one-line docstrings; layer: scripts.
Created: 2026-11-14 · Version: 1.0 (NirnAIn demo release)
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from code_index_features import FEATURE_MAP

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_FILE = REPOSITORY_ROOT / "INDEX.txt"
SOURCE_ROOTS = ("backend/app", "src", "backend/tests", "tests", "scripts", "supabase/tests")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}
# A JSDoc block directly followed by the function, const or class it documents.
JSDOC_SYMBOL = re.compile(
    r"/\*\*(?P<doc>(?:(?!\*/).)*?)\*/\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function\s+(?P<function>\w+)"
    r"|const\s+(?P<constant>\w+)\s*(?::[^=]+)?="
    r"|class\s+(?P<class_name>\w+))",
    re.S,
)
INDEX_HEADER = [
    "NirnAIn - code index",
    "",
    'How to use this file: Part 1 answers "where is the code for ...?" by feature, with the exact '
    "file and function. Part 2 lists every source file with its purpose and its functions, taken "
    "from the code's own headers and docstrings. Regenerate it with: "
    "python3.12 scripts/build_code_index.py",
    "",
    "Layers: the browser app (src/) calls the API (backend/app/api/v1), which uses repositories "
    "(backend/app/repositories, database access under the caller's row-level security) and domain "
    "models (backend/app/domain); planning runs in the optimizer (backend/app/optimization) as a "
    "background job (backend/app/jobs); the schema lives in supabase/migrations.",
    "",
]


@dataclass(frozen=True)
class SymbolEntry:
    """One class or function and the first line of its documentation."""

    name: str
    summary: str


def first_sentence(text: str | None) -> str:
    """Returns the first line of a docstring or JSDoc block, without comment markers."""
    if not text:
        return ""
    for raw_line in text.strip().splitlines():
        cleaned = raw_line.strip().lstrip("*").strip()
        if cleaned:
            return cleaned
    return ""


def source_files() -> list[str]:
    """Lists the Python and TypeScript source files under the indexed roots (caches skipped)."""
    found = (
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for root in SOURCE_ROOTS
        for path in (REPOSITORY_ROOT / root).rglob("*")
        if path.suffix in SOURCE_SUFFIXES and "__pycache__" not in path.parts
    )
    return sorted(found)


def python_symbols(source: str) -> tuple[str, list[SymbolEntry]]:
    """Returns a Python module's summary and its top-level classes and functions (with methods)."""
    tree = ast.parse(source)
    entries: list[SymbolEntry] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_summary = first_sentence(ast.get_docstring(node))
            entries.append(SymbolEntry(f"class {node.name}", class_summary))
            for member in node.body:
                is_function = isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                if is_function and not member.name.startswith("__"):
                    summary = first_sentence(ast.get_docstring(member))
                    entries.append(SymbolEntry(f"  {node.name}.{member.name}()", summary))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            entries.append(SymbolEntry(f"{node.name}()", first_sentence(ast.get_docstring(node))))
    return first_sentence(ast.get_docstring(tree)), entries


def typescript_symbols(source: str) -> tuple[str, list[SymbolEntry]]:
    """Returns a TypeScript file's summary (its header) and its documented functions and hooks."""
    header = re.match(r"\s*/\*\*(?P<doc>(?:(?!\*/).)*?)\*/", source, re.S)
    summary = first_sentence(header.group("doc")) if header else ""
    entries: list[SymbolEntry] = []
    for match in JSDOC_SYMBOL.finditer(source):
        if header and match.start() == header.start():
            continue
        name = match.group("function") or match.group("constant") or match.group("class_name")
        entries.append(SymbolEntry(f"{name}()", first_sentence(match.group("doc"))))
    return summary, entries


def symbol_exists(relative_path: str, symbol: str) -> bool:
    """Checks that a curated entry still points at a real file and a defined function or class."""
    path = REPOSITORY_ROOT / relative_path
    if not path.exists():
        return False
    if not symbol:
        return True
    # SQL functions are written `schema.name`, so allow an optional schema prefix.
    name = re.escape(symbol.rsplit(".", 1)[-1])
    definition = re.compile(rf"(def|class|function|const)\s+(?:\w+\.)?{name}\b")
    return bool(definition.search(path.read_text(encoding="utf-8", errors="replace")))


def curated_section() -> tuple[list[str], list[str]]:
    """Renders Part 1 (feature -> file and function) and lists entries that no longer resolve."""
    lines = ["PART 1 - WHERE IS THE CODE FOR ...?", ""]
    broken: list[str] = []
    for topic, entries in FEATURE_MAP:
        lines.append(topic)
        for question, relative_path, symbol, explanation in entries:
            if not symbol_exists(relative_path, symbol):
                broken.append(f"{relative_path} :: {symbol or '(file)'}")
            location = f"{relative_path} -> {symbol}()" if symbol else relative_path
            lines.append(f"  * {question} - {location}. {explanation}")
        lines.append("")
    return lines, broken


def file_entry_lines(relative_path: str) -> list[str]:
    """Renders one source file: its summary line, then one indented line per class or function."""
    source = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8", errors="replace")
    reader = python_symbols if relative_path.endswith(".py") else typescript_symbols
    try:
        summary, entries = reader(source)
    except SyntaxError:
        summary, entries = "(could not be parsed)", []
    lines = [f"{Path(relative_path).name} - {summary or 'no summary'}"]
    lines += [f"    {entry.name} - {entry.summary or 'no description'}" for entry in entries]
    return lines


def generated_section() -> list[str]:
    """Renders Part 2: every source file with its summary and its documented symbols."""
    lines = [
        "PART 2 - EVERY SOURCE FILE AND ITS FUNCTIONS "
        "(generated from the code's own headers and docstrings)",
        "",
    ]
    current_folder = None
    for relative_path in source_files():
        folder = str(Path(relative_path).parent)
        if folder != current_folder:
            lines += ["", f"[{folder}/]"]
            current_folder = folder
        lines += file_entry_lines(relative_path)
    migration_folder = REPOSITORY_ROOT / "supabase/migrations"
    migrations = sorted(path.name for path in migration_folder.glob("*.sql"))
    lines += [
        "",
        "[supabase/migrations/] - the database schema, applied in this order "
        "(never edited once merged)",
    ]
    lines += [f"    {name}" for name in migrations]
    return lines


def main() -> int:
    """Writes INDEX.txt; exits 1 if a curated entry points at a missing file or function."""
    curated, broken = curated_section()
    content = INDEX_HEADER + curated + generated_section()
    OUTPUT_FILE.write_text("\n".join(content) + "\n", encoding="utf-8")
    if broken:
        print("Curated entries that no longer resolve:\n  " + "\n  ".join(broken), file=sys.stderr)
        return 1
    print(f"wrote {OUTPUT_FILE.relative_to(REPOSITORY_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
