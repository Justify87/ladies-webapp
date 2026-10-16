from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".scss",
    ".html",
    ".htm",
    ".svg",
    ".xml",
    ".sh",
    ".command",
    ".ps1",
    ".yml",
    ".yaml",
    ".toml",
}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
}
REQUIRED_AREAS = ("ladies", "tools", "packaging", ".github/workflows")
SLASH_SUFFIXES = {".js", ".mjs", ".jsx", ".ts", ".tsx", ".css", ".scss"}
MARKUP_SUFFIXES = {".html", ".htm", ".svg", ".xml"}
JAVASCRIPT_SUFFIXES = {".js", ".mjs", ".jsx", ".ts", ".tsx"}

HISTORY_PATTERNS = (
    re.compile(r"\blegacy\b", re.IGNORECASE),
    re.compile(r"\bcompatibility\b", re.IGNORECASE),
    re.compile(r"\bbackwards?[- ]compatibility\b", re.IGNORECASE),
    re.compile(r"\bformer(?:ly)?\b", re.IGNORECASE),
    re.compile(r"\bpre[- ]?v\d", re.IGNORECASE),
    re.compile(r"\bexisting v\d", re.IGNORECASE),
    re.compile(
        r"\bold (?:implementation|code|path|flow|race|worker|caller|release|schema|database|endpoint|listener|layout|stylesheet|token|file)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bprevious (?:implementation|code|path|flow|architecture|version)\b", re.IGNORECASE),
    re.compile(r"\bone[- ]time rename\b", re.IGNORECASE),
    re.compile(r"\brefactor(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bworkaround\b", re.IGNORECASE),
)

PYTHON_CODE_COMMENT = re.compile(
    r"^(?:"
    r"async\s+def\s+\w+|def\s+\w+|class\s+\w+|"
    r"from\s+\S+\s+import\s+|import\s+\S+|"
    r"[A-Za-z_]\w*\s*=|if\s+.+:|elif\s+.+:|else\s*:|"
    r"for\s+.+:|while\s+.+:|with\s+.+:|try\s*:|except\b.+:|@\w+"
    r")"
)
JS_CODE_COMMENT = re.compile(
    r"^(?:"
    r"(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=|"
    r"function\s+[A-Za-z_$][\w$]*\s*\(|class\s+[A-Za-z_$][\w$]*|"
    r"import\s+.+\s+from\s+|export\s+(?:default\s+)?(?:function|class|const|let|var)\b"
    r")"
)
MACHINE_PREFIXES = (
    "noqa",
    "type: ignore",
    "pragma:",
    "nosec",
    "pylint:",
    "ruff:",
    "eslint",
    "prettier",
    "istanbul",
    "coverage:",
    "shellcheck",
)


def code_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in CODE_SUFFIXES:
            continue
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


def python_fragments(path: Path) -> list[tuple[int, str, str]]:
    source = path.read_text(encoding="utf-8")
    fragments: list[tuple[int, str, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                text = token.string[1:].strip()
                fragments.append((token.start[0], text, "comment"))
    except (IndentationError, tokenize.TokenError):
        pass

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return fragments

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant) or not isinstance(first.value.value, str):
            continue
        fragments.append((first.lineno, first.value.value.strip(), "docstring"))
    return fragments


def slash_fragments(source: str) -> list[tuple[int, str, str]]:
    fragments: list[tuple[int, str, str]] = []
    i = 0
    line = 1
    length = len(source)
    state = "code"
    quote = ""
    start_line = 1
    buffer: list[str] = []

    while i < length:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < length else ""
        if ch == "\n":
            line += 1

        if state == "code":
            if ch in {"'", '"', "`"}:
                state = "string"
                quote = ch
            elif ch == "/" and nxt == "/":
                state = "line_comment"
                start_line = line
                buffer = []
                i += 1
            elif ch == "/" and nxt == "*":
                state = "block_comment"
                start_line = line
                buffer = []
                i += 1
        elif state == "string":
            if ch == "\\":
                i += 1
            elif ch == quote:
                state = "code"
        elif state == "line_comment":
            if ch == "\n":
                fragments.append((start_line, "".join(buffer).strip(), "comment"))
                state = "code"
            else:
                buffer.append(ch)
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                fragments.append((start_line, "".join(buffer).strip(), "comment"))
                state = "code"
                i += 1
            else:
                buffer.append(ch)
        i += 1

    if state == "line_comment":
        fragments.append((start_line, "".join(buffer).strip(), "comment"))
    return fragments


def hash_fragments(source: str) -> list[tuple[int, str, str]]:
    fragments: list[tuple[int, str, str]] = []
    for line_no, raw in enumerate(source.splitlines(), 1):
        if line_no == 1 and raw.startswith("#!"):
            continue
        quote = None
        escaped = False
        for index, ch in enumerate(raw):
            if escaped:
                escaped = False
                continue
            if ch == "\\" and quote:
                escaped = True
                continue
            if ch in {"'", '"'}:
                if quote == ch:
                    quote = None
                elif quote is None:
                    quote = ch
                continue
            if ch == "#" and quote is None:
                fragments.append((line_no, raw[index + 1 :].strip(), "comment"))
                break
    return fragments


def html_fragments(source: str) -> list[tuple[int, str, str]]:
    fragments = slash_fragments(source)
    for match in re.finditer(r"<!--(.*?)-->", source, flags=re.DOTALL):
        line = source.count("\n", 0, match.start()) + 1
        fragments.append((line, match.group(1).strip(), "comment"))
    return fragments


def fragments(path: Path) -> list[tuple[int, str, str]]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return python_fragments(path)
    source = path.read_text(encoding="utf-8")
    if suffix in SLASH_SUFFIXES:
        return slash_fragments(source)
    if suffix in MARKUP_SUFFIXES:
        return html_fragments(source)
    return hash_fragments(source)


def machine_directive(text: str) -> bool:
    lowered = text.strip().lower()
    return any(lowered.startswith(prefix) for prefix in MACHINE_PREFIXES)


def violation_reason(text: str, kind: str, suffix: str) -> str | None:
    normalized = " ".join(text.split())
    if not normalized or machine_directive(normalized):
        return None
    for pattern in HISTORY_PATTERNS:
        if pattern.search(normalized):
            return f"historical/changelog wording: {pattern.pattern}"
    if kind == "comment":
        if suffix == ".py" and PYTHON_CODE_COMMENT.match(normalized):
            return "commented-out Python code"
        if suffix in JAVASCRIPT_SUFFIXES and JS_CODE_COMMENT.match(normalized):
            return "commented-out JavaScript/TypeScript code"
    return None


def main() -> int:
    files = code_files()
    if not files:
        print("comment hygiene: no code files found", file=sys.stderr)
        return 1

    area_counts: Counter[str] = Counter()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        for area in REQUIRED_AREAS:
            if relative == area or relative.startswith(area + "/"):
                area_counts[area] += 1
                break

    missing_areas = [area for area in REQUIRED_AREAS if area_counts[area] == 0]
    if missing_areas:
        print(f"comment hygiene: unscanned code area(s): {', '.join(missing_areas)}", file=sys.stderr)
        return 1

    violations: list[str] = []
    fragment_count = 0
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        for line, text, kind in fragments(path):
            fragment_count += 1
            reason = violation_reason(text, kind, path.suffix.lower())
            if reason:
                preview = " ".join(text.split())[:180]
                violations.append(f"{relative}:{line}: {reason}: {preview}")

    print(
        "comment hygiene: "
        f"scanned {len(files)} code files and {fragment_count} comment/docstring fragments; "
        + ", ".join(f"{area}={area_counts[area]}" for area in REQUIRED_AREAS)
    )
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
