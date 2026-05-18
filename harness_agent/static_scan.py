from __future__ import annotations

import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
from time import time


STATE_DIRS = {".git", ".tasks", ".team", ".harness", "__pycache__", ".venv", "venv", "node_modules", "vendor"}
LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".dart": "dart",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".php": "php",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
}
SUPPORTED_LANGUAGES = sorted(set(LANGUAGE_BY_SUFFIX.values()))


@dataclass(frozen=True)
class SourceFile:
    path: Path
    module: str
    language: str
    text: str
    syntax_error: str = ""
    tree: ast.AST | None = None


class StaticScanner:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def scan(self) -> dict:
        files = self._source_files()
        report = {
            "generatedAt": time(),
            "supportedLanguages": SUPPORTED_LANGUAGES,
            "scannedFiles": [
                {"path": self._rel(source.path), "language": source.language}
                for source in files
            ],
            "syntaxErrors": self._syntax_errors(files),
            "duplicateDefinitions": self._duplicate_definitions(files),
            "duplicateAssignments": self._duplicate_assignments(files),
            "circularImports": self._circular_imports(files),
            "possiblyUnusedFiles": self._possibly_unused_files(files),
        }
        report["blockingIssueCount"] = (
            len(report["syntaxErrors"])
            + len(report["duplicateDefinitions"])
            + len(report["circularImports"])
        )
        return report

    def write_report(self, name: str, report: dict) -> Path:
        target = self.root / ".harness" / "static-scan" / f"{name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def _source_files(self) -> list[SourceFile]:
        sources: list[SourceFile] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or self._is_state_path(path):
                continue
            language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if not language:
                continue
            rel = path.relative_to(self.root)
            module = self._module_name(rel)
            text = path.read_text(encoding="utf-8", errors="replace")
            if language == "python":
                try:
                    sources.append(SourceFile(path, module, language, text, tree=ast.parse(text, filename=str(rel))))
                except SyntaxError as exc:
                    sources.append(SourceFile(path, module, language, text, syntax_error=str(exc)))
                continue
            sources.append(SourceFile(path, module, language, text, syntax_error=self._generic_syntax_error(text)))
        return sources

    def _syntax_errors(self, files: list[SourceFile]) -> list[dict[str, str]]:
        return [
            {"path": self._rel(source.path), "language": source.language, "error": source.syntax_error}
            for source in files
            if source.syntax_error
        ]

    def _duplicate_definitions(self, files: list[SourceFile]) -> list[dict[str, object]]:
        findings: list[dict[str, object]] = []
        for source in files:
            seen: dict[tuple[str, str], list[int]] = {}
            for kind, name, line in self._definitions(source):
                seen.setdefault((kind, name), []).append(line)
            for (kind, name), lines in sorted(seen.items()):
                if len(lines) > 1:
                    findings.append(
                        {
                            "path": self._rel(source.path),
                            "language": source.language,
                            "kind": kind,
                            "name": name,
                            "lines": lines,
                        }
                    )
        return findings

    def _duplicate_assignments(self, files: list[SourceFile]) -> list[dict[str, object]]:
        findings: list[dict[str, object]] = []
        for source in files:
            seen: dict[str, list[int]] = {}
            for name, line in self._assignments(source):
                if name.startswith("_"):
                    continue
                seen.setdefault(name, []).append(line)
            for name, lines in sorted(seen.items()):
                if len(lines) > 1:
                    findings.append(
                        {
                            "path": self._rel(source.path),
                            "language": source.language,
                            "name": name,
                            "lines": lines,
                        }
                    )
        return findings

    def _circular_imports(self, files: list[SourceFile]) -> list[list[str]]:
        graph = {
            source.module: sorted(self._local_imports(source, files))
            for source in files
            if not source.syntax_error
        }
        cycles: set[tuple[str, ...]] = set()

        def visit(node: str, stack: list[str]) -> None:
            if node in stack:
                cycle = stack[stack.index(node) :] + [node]
                cycles.add(self._canonical_cycle(cycle))
                return
            for child in graph.get(node, []):
                visit(child, [*stack, node])

        for node in graph:
            visit(node, [])
        return [list(cycle) for cycle in sorted(cycles)]

    def _possibly_unused_files(self, files: list[SourceFile]) -> list[str]:
        imported: set[str] = set()
        for source in files:
            imported.update(self._local_imports(source, files))
        unused = []
        for source in files:
            rel = self._rel(source.path)
            if self._is_entry_or_test_path(rel) or source.module in imported:
                continue
            unused.append(rel)
        return sorted(unused)

    def _definitions(self, source: SourceFile) -> list[tuple[str, str, int]]:
        if source.language == "python" and source.tree:
            return self._python_definitions(source)
        return self._regex_definitions(source)

    def _python_definitions(self, source: SourceFile) -> list[tuple[str, str, int]]:
        assert source.tree is not None
        definitions: list[tuple[str, str, int]] = []
        for node in ast.walk(source.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            parent = self._parent_qualifier(source.tree, node)
            name = f"{parent}.{node.name}" if parent else node.name
            definitions.append((kind, name, node.lineno))
        return definitions

    def _regex_definitions(self, source: SourceFile) -> list[tuple[str, str, int]]:
        text = self._strip_comments_and_strings(source.text, source.language)
        patterns = self._definition_patterns(source.language)
        definitions: list[tuple[str, str, int]] = []
        for kind, pattern in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                name = next(group for group in match.groups() if group)
                definitions.append((kind, name, self._line_for_offset(text, match.start())))
        return definitions

    def _definition_patterns(self, language: str) -> list[tuple[str, str]]:
        if language in {"javascript", "typescript"}:
            return [
                ("class", r"\bclass\s+([A-Za-z_$][\w$]*)\b"),
                ("function", r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\("),
                ("function", r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"),
            ]
        if language == "go":
            return [
                ("type", r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"),
                ("function", r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),
            ]
        if language in {"c", "cpp"}:
            return [
                ("class", r"\b(?:class|struct)\s+([A-Za-z_]\w*)\b"),
                ("function", r"^\s*(?:[A-Za-z_][\w:<>,\s\*&~]*\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
            ]
        if language == "java":
            return [
                ("class", r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)\b"),
                ("function", r"^\s*(?:public|private|protected|static|final|native|synchronized|abstract|\s)+[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
            ]
        if language == "dart":
            return [
                ("class", r"\b(?:class|mixin|enum|extension)\s+([A-Za-z_]\w*)\b"),
                ("function", r"^\s*(?:[A-Za-z_][\w<>,?]*\s+)+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:async\s*)?\{"),
            ]
        if language == "php":
            return [
                ("class", r"\b(?:class|interface|trait|enum)\s+([A-Za-z_]\w*)\b"),
                ("function", r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
            ]
        if language == "csharp":
            return [
                ("class", r"\b(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)\b"),
                ("function", r"^\s*(?:public|private|protected|internal|static|async|virtual|override|sealed|partial|extern|\s)+[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"),
            ]
        return []

    def _assignments(self, source: SourceFile) -> list[tuple[str, int]]:
        if source.language == "python" and isinstance(source.tree, ast.Module):
            assignments: list[tuple[str, int]] = []
            for node in source.tree.body:
                for name in self._python_assigned_names(node):
                    assignments.append((name, getattr(node, "lineno", 0)))
            return assignments
        text = self._strip_comments_and_strings(source.text, source.language)
        patterns = self._assignment_patterns(source.language)
        assignments = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                assignments.append((match.group(1), self._line_for_offset(text, match.start())))
        return assignments

    def _assignment_patterns(self, language: str) -> list[str]:
        if language in {"javascript", "typescript"}:
            return [r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="]
        if language == "go":
            return [r"^\s*var\s+([A-Za-z_]\w*)\b", r"^\s*const\s+([A-Za-z_]\w*)\b"]
        if language in {"c", "cpp", "java", "dart", "csharp"}:
            return [r"^\s*(?:public|private|protected|internal|static|final|const|readonly|extern|\s)*(?:[A-Za-z_][\w<>\[\], ?\*]+\s+)+([A-Za-z_]\w*)\s*="]
        if language == "php":
            return [r"^\s*(?:public|private|protected|static|\s)*\$([A-Za-z_]\w*)\s*="]
        return []

    def _local_imports(self, source: SourceFile, files: list[SourceFile]) -> set[str]:
        if source.language == "python" and source.tree:
            return self._python_local_imports(source, files)
        references = self._import_references(source)
        imports: set[str] = set()
        for reference in references:
            imports.update(self._resolve_reference(source, reference, files))
        imports.discard(source.module)
        return imports

    def _python_local_imports(self, source: SourceFile, files: list[SourceFile]) -> set[str]:
        assert source.tree is not None
        module_names = {item.module for item in files}
        imports: set[str] = set()
        for node in ast.walk(source.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.update(self._matching_modules(alias.name, module_names))
            elif isinstance(node, ast.ImportFrom):
                base = self._resolve_python_import_from(source.module, node)
                if base:
                    imports.update(self._matching_modules(base, module_names))
        imports.discard(source.module)
        return imports

    def _import_references(self, source: SourceFile) -> list[str]:
        text = self._strip_comments_and_strings(source.text, source.language, preserve_import_strings=True)
        patterns = self._import_patterns(source.language)
        references: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.MULTILINE):
                references.append(next(group for group in match.groups() if group))
        return references

    def _import_patterns(self, language: str) -> list[str]:
        if language in {"javascript", "typescript"}:
            return [
                r"\bimport\s+(?:[^'\"]+\s+from\s+)?['\"]([^'\"]+)['\"]",
                r"\bexport\s+[^'\"]+\s+from\s+['\"]([^'\"]+)['\"]",
                r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            ]
        if language in {"c", "cpp"}:
            return [r"^\s*#\s*include\s+\"([^\"]+)\""]
        if language == "go":
            return [r"^\s*import\s+\"([^\"]+)\"", r"^\s*\"([^\"]+)\""]
        if language == "java":
            return [r"^\s*import\s+(?:static\s+)?([A-Za-z_][\w.]*)(?:\.\*)?;"]
        if language == "dart":
            return [r"\b(?:import|export|part)\s+['\"]([^'\"]+)['\"]"]
        if language == "php":
            return [r"\b(?:require|require_once|include|include_once)\s*\(?\s*['\"]([^'\"]+)['\"]"]
        if language == "csharp":
            return [r"^\s*using\s+([A-Za-z_][\w.]*)\s*;"]
        return []

    def _resolve_reference(self, source: SourceFile, reference: str, files: list[SourceFile]) -> set[str]:
        if reference.startswith(".") or "/" in reference or "\\" in reference:
            return self._resolve_path_reference(source, reference, files)
        module_names = {item.module for item in files}
        return self._matching_modules(reference.replace("/", "."), module_names)

    def _resolve_path_reference(self, source: SourceFile, reference: str, files: list[SourceFile]) -> set[str]:
        base = source.path.parent
        candidate = (base / reference).resolve()
        candidates = [candidate]
        if not candidate.suffix:
            for suffix in LANGUAGE_BY_SUFFIX:
                candidates.append(candidate.with_suffix(suffix))
            candidates.extend(candidate / f"index{suffix}" for suffix in (".js", ".ts", ".tsx", ".jsx"))
            candidates.extend(candidate / f"mod{suffix}" for suffix in (".rs",))
        resolved: set[str] = set()
        path_to_module = {item.path.resolve(): item.module for item in files}
        for item in candidates:
            module = path_to_module.get(item)
            if module:
                resolved.add(module)
        return resolved

    def _generic_syntax_error(self, text: str) -> str:
        cleaned = self._strip_comments_and_strings(text, "generic")
        pairs = {"(": ")", "[": "]", "{": "}"}
        closing = {value: key for key, value in pairs.items()}
        stack: list[tuple[str, int]] = []
        for index, char in enumerate(cleaned):
            if char in pairs:
                stack.append((char, index))
            elif char in closing:
                if not stack or stack[-1][0] != closing[char]:
                    return f"unmatched {char!r} near line {self._line_for_offset(cleaned, index)}"
                stack.pop()
        if stack:
            char, index = stack[-1]
            return f"unclosed {char!r} opened near line {self._line_for_offset(cleaned, index)}"
        return ""

    def _strip_comments_and_strings(
        self,
        text: str,
        language: str,
        preserve_import_strings: bool = False,
    ) -> str:
        if preserve_import_strings:
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            text = re.sub(r"//.*", "", text)
            text = re.sub(r"#(?!\s*include).*", "", text)
            return text
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"//.*", "", text)
        if language in {"python", "php", "generic"}:
            text = re.sub(r"#.*", "", text)
        text = re.sub(r"'''(?:.|\n)*?'''", "''", text)
        text = re.sub(r'"""(?:.|\n)*?"""', '""', text)
        text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
        text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
        text = re.sub(r"`(?:\\.|[^`\\])*`", "``", text)
        return text

    def _matching_modules(self, name: str, module_names: set[str]) -> set[str]:
        matches = set()
        for candidate in module_names:
            if candidate == name or candidate.startswith(f"{name}.") or candidate.endswith(f".{name}"):
                matches.add(candidate)
        return matches

    def _resolve_python_import_from(self, module_name: str, node: ast.ImportFrom) -> str:
        if node.level <= 0:
            return node.module or ""
        package_parts = module_name.split(".")[:-1]
        base_parts = package_parts[: max(len(package_parts) - node.level + 1, 0)]
        if node.module:
            base_parts.extend(node.module.split("."))
        return ".".join(part for part in base_parts if part)

    def _python_assigned_names(self, node: ast.AST) -> list[str]:
        targets: list[ast.expr] = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names: list[str] = []
        for target in targets:
            names.extend(self._python_target_names(target))
        return names

    def _python_target_names(self, target: ast.expr) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for item in target.elts:
                names.extend(self._python_target_names(item))
            return names
        return []

    def _parent_qualifier(self, tree: ast.AST, target: ast.AST) -> str:
        parents: list[str] = []

        def walk(node: ast.AST, stack: list[str]) -> bool:
            if node is target:
                parents.extend(stack)
                return True
            next_stack = stack
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                next_stack = [*stack, node.name]
            for child in ast.iter_child_nodes(node):
                if walk(child, next_stack):
                    return True
            return False

        walk(tree, [])
        return ".".join(parents)

    def _canonical_cycle(self, cycle: list[str]) -> tuple[str, ...]:
        body = cycle[:-1]
        rotations = [tuple(body[index:] + body[:index]) for index in range(len(body))]
        canonical = min(rotations)
        return (*canonical, canonical[0])

    def _module_name(self, rel: Path) -> str:
        return ".".join(rel.with_suffix("").parts)

    def _line_for_offset(self, text: str, offset: int) -> int:
        return text.count("\n", 0, offset) + 1

    def _is_entry_or_test_path(self, rel: str) -> bool:
        name = Path(rel).name
        return (
            rel.startswith("tests/")
            or rel.endswith("__init__.py")
            or name in {"main.py", "app.py", "index.js", "index.ts", "main.go", "main.dart", "Program.cs"}
        )

    def _is_state_path(self, path: Path) -> bool:
        return bool(set(path.relative_to(self.root).parts) & STATE_DIRS)

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()
