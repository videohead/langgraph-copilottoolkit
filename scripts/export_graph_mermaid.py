#!/usr/bin/env python3
"""Export Mermaid diagrams for all graphs registered in langgraph.json."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load_registry(registry_file: Path) -> dict[str, str]:
    payload = json.loads(registry_file.read_text(encoding="utf-8"))
    graph_map = payload.get("graphs", {})
    if not isinstance(graph_map, dict):
        raise ValueError("'graphs' in langgraph.json must be an object")

    result: dict[str, str] = {}
    for graph_id, graph_ref in graph_map.items():
        if isinstance(graph_id, str) and isinstance(graph_ref, str):
            graph_id = graph_id.strip()
            graph_ref = graph_ref.strip()
            if graph_id and graph_ref:
                result[graph_id] = graph_ref
    return result


def _load_symbol(module_file: Path, symbol_name: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, module_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_file}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, symbol_name):
        raise AttributeError(f"Symbol '{symbol_name}' not found in {module_file}")
    return getattr(module, symbol_name)


def _graph_to_mermaid(graph_obj) -> str:
    draw_target = graph_obj

    if hasattr(graph_obj, "get_graph"):
        try:
            draw_target = graph_obj.get_graph(xray=True)
        except TypeError:
            draw_target = graph_obj.get_graph()

    if not hasattr(draw_target, "draw_mermaid"):
        raise RuntimeError("Graph object does not expose draw_mermaid()")

    mermaid = draw_target.draw_mermaid()
    if not isinstance(mermaid, str) or not mermaid.strip():
        raise RuntimeError("draw_mermaid() returned empty output")
    return mermaid


def _parse_graph_ref(graph_ref: str) -> tuple[str, str]:
    try:
        module_rel_path, symbol_name = graph_ref.split(":", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid graph ref '{graph_ref}', expected './path.py:symbol'") from exc

    module_rel_path = module_rel_path.strip()
    symbol_name = symbol_name.strip()
    if not module_rel_path or not symbol_name:
        raise ValueError(f"Invalid graph ref '{graph_ref}'")
    return module_rel_path, symbol_name


def export_mermaid_files(registry_file: Path, output_dir: Path, graph_ids: set[str] | None) -> int:
    registry = _load_registry(registry_file)
    repo_root = registry_file.parent.resolve()

    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    output_dir.mkdir(parents=True, exist_ok=True)

    ok_count = 0
    for graph_id, graph_ref in registry.items():
        if graph_ids and graph_id not in graph_ids:
            continue

        try:
            module_rel_path, symbol_name = _parse_graph_ref(graph_ref)
            module_file = (repo_root / module_rel_path).resolve()
            if not module_file.exists():
                raise FileNotFoundError(f"Module file does not exist: {module_file}")

            symbol = _load_symbol(module_file, symbol_name, f"graph_export_{graph_id}")
            mermaid = _graph_to_mermaid(symbol)

            output_file = output_dir / f"{graph_id}.mmd"
            output_file.write_text(mermaid, encoding="utf-8")
            print(f"OK  {graph_id} -> {output_file}")
            ok_count += 1
        except Exception as exc:  # noqa: BLE001
            print(f"ERR {graph_id}: {exc}", file=sys.stderr)

    return ok_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Mermaid .mmd files for registered LangGraph graphs")
    parser.add_argument(
        "--registry",
        default="langgraph.json",
        help="Path to langgraph.json (default: langgraph.json)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where <graph_id>.mmd files are written",
    )
    parser.add_argument(
        "--graph",
        action="append",
        dest="graphs",
        help="Optional graph id to export (repeatable)",
    )
    args = parser.parse_args()

    # Disable MCP tool bootstrapping while importing graph modules.
    os.environ.setdefault("MCP_FILESYSTEM_ENABLED", "false")

    registry_file = Path(args.registry).resolve()
    if not registry_file.exists():
        print(f"Registry file not found: {registry_file}", file=sys.stderr)
        return 1

    graph_ids = set(args.graphs or [])
    output_dir = Path(args.output_dir).resolve()

    ok_count = export_mermaid_files(registry_file, output_dir, graph_ids)
    if ok_count == 0:
        print("No Mermaid files were generated.", file=sys.stderr)
        return 1

    print(f"Generated Mermaid files: {ok_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())