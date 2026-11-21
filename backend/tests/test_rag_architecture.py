"""RAG 依赖方向回归检查。"""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("directory", "forbidden"),
    [
        (
            "app/rag",
            (
                "app.api",
                "app.schemas",
                "app.services",
                "app.models",
                "app.repositories",
                "app.worker",
            ),
        ),
        ("app/rag/indexing", ("app.rag.pipelines",)),
        ("app/rag/retrieval", ("app.rag.pipelines",)),
    ],
)
def test_rag_dependencies_follow_layer_boundaries(directory, forbidden):
    violations = []
    for path in (BACKEND / directory).rglob("*.py"):
        relative = path.relative_to(BACKEND)
        package = ".".join(relative.parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                dependencies = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = resolve_name("." * node.level + module, package)
                dependencies = [
                    module,
                    *[f"{module}.{alias.name}" for alias in node.names],
                ]
            else:
                continue
            for dependency in dependencies:
                if any(
                    dependency == prefix or dependency.startswith(f"{prefix}.")
                    for prefix in forbidden
                ):
                    violations.append(f"{relative}:{node.lineno} imports {dependency}")
    assert not violations, "\n".join(violations)
