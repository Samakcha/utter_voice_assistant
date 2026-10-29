"""Unit test verifying strict architectural separation between engine and interface."""

import ast
from pathlib import Path


def test_engine_imports_nothing_from_interface():
    project_root = Path(__file__).resolve().parent.parent
    engine_dir = project_root / "engine"
    assert engine_dir.is_dir(), "engine directory not found"

    violations = []

    for py_file in engine_dir.glob("**/*.py"):
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("interface"):
                        violations.append(f"{py_file.name}: imports '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("interface"):
                    violations.append(f"{py_file.name}: imports from '{node.module}'")

    assert not violations, f"Architectural violation! engine/ must not import from interface/: {violations}"
