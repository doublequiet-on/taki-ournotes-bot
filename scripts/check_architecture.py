# L3
# Input: Source checkout and reviewed, exact architecture exceptions.
# Output: Nonzero exit for new violations, stale exceptions or invalid policy.
# Pos: Scripts / development and CI gate; see ../docs/ARCHITECTURE.md.
# Effects/Dependencies: Standard-library AST and read-only files; never imports bot code.
"""Ratchet import boundaries without hiding existing debt behind directory ignores."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sys

PACKAGE = "ournotes_bot"
NETWORK = ("urllib", "http", "socket", "requests", "aiohttp", "httpx")


def under(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def pure(name: str) -> bool:
    parts = name.split(".")
    return (under(name, PACKAGE + ".domain")
            or len(parts) >= 3 and parts[1] in {"query", "rendering"}
            and (parts[2] in {"contracts", "pagination"} or parts[-1] == "contracts")
            or name in {PACKAGE + ".sources.json_validation",
                        PACKAGE + ".sources.errors",
                        PACKAGE + ".rendering.support_summary"})


def common_source(name: str) -> bool:
    return any(under(name, PACKAGE + ".sources.haneoka." + leaf)
               for leaf in ("transport", "identity"))


def area(name: str) -> str:
    if not under(name, PACKAGE):
        return "external"
    if pure(name):
        return "pure"
    if common_source(name):
        return "common-source"
    suffix = name[len(PACKAGE) + 1:]
    for prefix in ("sources", "query", "rendering", "natural_query", "platforms", "application"):
        if under(suffix, prefix):
            return prefix
    return {"data": "catalog", "commands": "query-entry", "structured_query": "query-entry",
            "visuals": "rendering-entry", "main": "bootstrap", "bootstrap": "bootstrap",
            "ai_query": "natural_query", "update_notice": "platforms"}.get(suffix, "support")


@dataclass(frozen=True, order=True)
class Edge:
    source: str
    target: str
    symbol: str = ""


@dataclass(frozen=True, order=True)
class Violation:
    rule: str
    source: str
    target: str
    symbol: str = ""


def collect(source_root: Path) -> tuple[set[Edge], dict[Edge, int]]:
    """Include lazy/type-only imports and statically named dynamic imports."""
    files = {}
    for path in sorted((source_root / PACKAGE).rglob("*.py")):
        parts = list(path.relative_to(source_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        files[".".join(parts)] = path
    if not files:
        raise ValueError("missing Python source package")
    edges, lines = set(), {}
    for module, path in files.items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        aliases = {}

        def add(target, symbol, node):
            edge = Edge(module, target, symbol)
            edges.add(edge)
            lines.setdefault(edge, node.lineno)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name, "", node)
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                target = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                          if node.level else node.module or "")
                for alias in node.names:
                    candidate = target + "." + alias.name
                    if candidate in files:
                        add(candidate, "", node)
                        aliases[alias.asname or alias.name] = candidate
                    else:
                        add(target, "", node)
                        if alias.name.startswith("_"):
                            add(target, alias.name, node)
                        # import_module may be imported under any local alias.
                        aliases[alias.asname or alias.name] = candidate
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                parts, value = [], node
                while isinstance(value, ast.Attribute):
                    parts.insert(0, value.attr)
                    value = value.value
                if isinstance(value, ast.Name) and value.id in aliases:
                    target = aliases[value.id]
                    while parts and target + "." + parts[0] in files:
                        target += "." + parts.pop(0)
                    if target in files and parts and parts[0].startswith("_") and not parts[0].startswith("__"):
                        add(target, parts[0], node)
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                head, _, tail = name.partition(".")
                resolved = aliases.get(head, head) + ("." + tail if tail else "")
                if resolved in {"importlib.import_module", "__import__"}:
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        target = node.args[0].value
                        if target.startswith("."):
                            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant) or not isinstance(node.args[1].value, str):
                                add("<dynamic-import>", "", node)
                                continue
                            target = importlib.util.resolve_name(target, node.args[1].value)
                        add(target, "", node)
                    else:
                        add("<dynamic-import>", "", node)
                elif name == "getattr" and len(node.args) >= 2:
                    value, attr = node.args[:2]
                    if isinstance(value, ast.Name) and aliases.get(value.id) in files and isinstance(attr, ast.Constant) and isinstance(attr.value, str) and attr.value.startswith("_"):
                        add(aliases[value.id], attr.value, node)
    return edges, lines


def violations(edges: set[Edge]) -> set[Violation]:
    result = set()
    for edge in edges:
        source, target = area(edge.source), area(edge.target)

        def fail(rule):
            result.add(Violation(rule, edge.source, edge.target,
                                 edge.symbol if rule == "private-boundary" else ""))

        if edge.target == "<dynamic-import>":
            fail("dynamic-import")
        if source not in {"platforms", "bootstrap"} and (under(edge.target, "botpy") or target == "platforms"):
            fail("core-no-qq")
        if source not in {"platforms", "bootstrap"} and target == "bootstrap":
            fail("no-bootstrap-backedge")
        if source == "pure" and (under(edge.target, PACKAGE) and target != "pure"
                                 or not under(edge.target, PACKAGE)
                                 and edge.target.split(".")[0] not in sys.stdlib_module_names
                                 or any(under(edge.target, prefix) for prefix in NETWORK)):
            fail("pure-contract")
        if source == "common-source" and under(edge.target, PACKAGE) and target not in {"common-source", "pure"}:
            fail("source-foundation")
        if source == "sources" and (target not in {"sources", "common-source", "pure", "external", "support"}
                                    or target == "support" and edge.target != PACKAGE + ".config"):
            fail("source-direction")
        if source in {"query", "query-entry"} and (target in {"rendering", "rendering-entry", "natural_query", "sources"}
                                                   or source == "query" and target == "query-entry"
                                                   or target == "common-source"
                                                   or any(under(edge.target, prefix) for prefix in NETWORK)):
            fail("query-direction")
        if source in {"rendering", "rendering-entry"} and target in {"sources", "common-source", "query", "query-entry", "natural_query"}:
            fail("rendering-direction")
        # Sibling implementations are part of the same subsystem. Haneoka's
        # concrete transport borrowing and legacy facade borrowing are debt.
        cross = source != target or source in {"sources", "query-entry"} and edge.source != edge.target
        if edge.symbol.startswith("_") and cross and under(edge.target, PACKAGE):
            fail("private-boundary")
    return result


def load_exceptions(path: Path) -> set[Violation]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate policy key: " + key)
            result[key] = value
        return result

    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(payload, dict):
        raise ValueError("policy must be a JSON object")
    if set(payload) != {"schema", "debts", "exceptions"} or payload["schema"] != 1:
        raise ValueError("invalid architecture policy schema")
    if not isinstance(payload["debts"], dict) or not isinstance(payload["exceptions"], list):
        raise ValueError("invalid debt or exception collection")
    result = set()
    hard = {"core-no-qq", "pure-contract", "source-foundation", "no-bootstrap-backedge"}
    for debt in payload["debts"].values():
        if not isinstance(debt, dict) or set(debt) != {"reason", "owner", "remove_when"} or any(not isinstance(v, str) or not v.strip() for v in debt.values()):
            raise ValueError("each debt requires reason, owner and removal condition")
    used = set()
    for row in payload["exceptions"]:
        if not isinstance(row, list) or len(row) != 5 or any(not isinstance(value, str) for value in row):
            raise ValueError("each exception requires an exact edge, reason, owner and removal condition")
        rule, source, target, symbol, debt = row
        if not all((rule.strip(), source.strip(), target.strip())) or debt not in payload["debts"]:
            raise ValueError("invalid exception edge or debt reference")
        used.add(debt)
        if any(char in value for value in (source, target, symbol) for char in "*?[]"):
            raise ValueError("wildcard exceptions are forbidden")
        if rule in hard:
            raise ValueError("hard boundary cannot be excepted: " + rule)
        violation = Violation(rule, PACKAGE + "." + source, PACKAGE + "." + target, symbol)
        if violation in result:
            raise ValueError("duplicate architecture exception")
        result.add(violation)
    if used != set(payload["debts"]):
        raise ValueError("remove unused debt metadata")
    return result


def check(root: Path) -> tuple[set[Violation], set[Violation], dict[Edge, int]]:
    edges, lines = collect(root / "src")
    actual = violations(edges)
    allowed = load_exceptions(root / "architecture_exceptions.json")
    return actual - allowed, allowed - actual, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        new, stale, lines = check(args.root)
    except (ValueError, SyntaxError, OSError) as exc:
        print("Architecture policy error:", exc)
        return 1
    for item in sorted(new):
        suffix = "." + item.symbol if item.symbol else ""
        line = lines.get(Edge(item.source, item.target, item.symbol), "?")
        print(f"NEW {item.rule}: {item.source}:{line} -> {item.target}{suffix}")
    for item in sorted(stale):
        print(f"STALE remove exception: {item.rule}: {item.source} -> {item.target} {item.symbol}")
    if new or stale:
        return 1
    print("Architecture boundaries OK; exact legacy exceptions remain visible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
