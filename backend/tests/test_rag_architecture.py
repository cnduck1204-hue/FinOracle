"""RAG 依赖方向与迁移后的公共入口回归检查。"""

import ast
from importlib import import_module
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


@pytest.mark.parametrize(
    ("previous", "current", "names"),
    [
        (
            "app.schemas.document",
            "app.rag.parsing.parameters",
            ("ParseOptionsInput", "ParseRequest"),
        ),
        (
            "app.rag.pipelines.visual",
            "app.rag.retrieval.visual",
            ("VisualHybridRetriever", "VisualOnlyRetriever"),
        ),
        (
            "app.rag.pipelines.text_index",
            "app.rag.indexing.text_index",
            ("TextIndexer", "plain_entries"),
        ),
        (
            "app.rag.pipelines.base",
            "app.rag.models",
            ("SourceFile", "ParsedUnits", "IndexTarget", "StoredUnit", "IngestRun"),
        ),
    ],
)
def test_existing_imports_resolve_to_the_moved_implementations(
    previous, current, names
):
    previous_module = import_module(previous)
    current_module = import_module(current)
    for name in names:
        assert getattr(previous_module, name) is getattr(current_module, name)
