from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

MAX_COMPLEXITY = 10
MAX_FILE_LINES = 500
MAX_FUNCTION_LINES = 50
MAX_NESTING_DEPTH = 3
MAX_POSITIONAL_PARAMETERS = 3
SOURCE_ROOTS = (Path("src"), Path("tests"), Path("alembic"), Path("scripts"))
POSITIONAL_EXEMPTIONS = {(Path("alembic/env.py"), "include_object")}
BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
    ast.Match,
)
NESTING_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def python_files() -> tuple[Path, ...]:
    return tuple(path for root in SOURCE_ROOTS for path in root.rglob("*.py"))


def nonblank_lines(lines: list[str], node: ast.AST) -> int:
    end_line = getattr(node, "end_lineno", node.lineno)
    return sum(bool(line.strip()) for line in lines[node.lineno - 1 : end_line])


def positional_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    arguments = [*node.args.posonlyargs, *node.args.args]
    if arguments and arguments[0].arg in {"self", "cls"}:
        arguments = arguments[1:]
    return len(arguments)


def complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    score = 1
    for child in ast.walk(node):
        score += isinstance(child, BRANCH_NODES)
        if isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
    return score


def nesting_depth(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return _node_depth(node, 0)


def _node_depth(node: ast.AST, depth: int) -> int:
    maximum = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        child_depth = depth + 1 if isinstance(child, NESTING_NODES) else depth
        maximum = max(maximum, _node_depth(child, child_depth))
    return maximum


def function_violations(path: Path, lines: list[str], node: ast.AST) -> list[Violation]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    found: list[Violation] = []
    _append_limit(
        found,
        path,
        node,
        label="function lines",
        value=nonblank_lines(lines, node),
        limit=MAX_FUNCTION_LINES,
    )
    exempt = (path, node.name) in POSITIONAL_EXEMPTIONS
    if not exempt:
        _append_limit(
            found,
            path,
            node,
            label="positional parameters",
            value=positional_count(node),
            limit=MAX_POSITIONAL_PARAMETERS,
        )
    _append_limit(
        found,
        path,
        node,
        label="cyclomatic complexity",
        value=complexity(node),
        limit=MAX_COMPLEXITY,
    )
    _append_limit(
        found,
        path,
        node,
        label="nesting depth",
        value=nesting_depth(node),
        limit=MAX_NESTING_DEPTH,
    )
    return found


def _append_limit(
    found: list[Violation],
    path: Path,
    node: ast.AST,
    *,
    label: str,
    value: int,
    limit: int,
) -> None:
    if value > limit:
        found.append(Violation(path, node.lineno, f"{label} {value} exceeds {limit}"))


def inspect_file(path: Path) -> list[Violation]:
    lines = path.read_text(encoding="utf-8").splitlines()
    found = []
    if len(lines) > MAX_FILE_LINES:
        found.append(Violation(path, 1, f"file lines {len(lines)} exceeds {MAX_FILE_LINES}"))
    tree = ast.parse("\n".join(lines), filename=str(path))
    for node in ast.walk(tree):
        found.extend(function_violations(path, lines, node))
    return found


def main() -> int:
    violations = [item for path in python_files() for item in inspect_file(path)]
    if violations:
        print("\n".join(item.render() for item in violations))
        return 1
    print("Code metrics passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
