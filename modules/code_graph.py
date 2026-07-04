"""
code_graph.py — 경량 코드베이스 지식그래프 (2026-07-03)

목적: "이 함수 누가 부르나", "이 모듈 누가 import 하나" 같은 걸 grep 여러 번
돌리는 대신 SQLite 쿼리 한 번으로 해결. codebase-memory-mcp(외부 MCP 서버,
tree-sitter+LSP 158개 언어 지원) 검토 후 — Hermes는 거의 100% Python이라
표준 라이브러리 `ast` 모듈만으로 충분하다고 판단해 자체 구현.

특징:
  - 신규 의존성 0개 (ast, sqlite3 전부 표준 라이브러리)
  - MCP 서버 아님 — 그냥 스크립트. Bash로 직접 호출해서 사용
  - 인덱스는 Scripts/ 안에 저장 (~/Applications/ 경계 안 지킴)

사용법:
  python3 modules/code_graph.py --rebuild
  python3 modules/code_graph.py --callers compute_farming_signals
  python3 modules/code_graph.py --importers deriver_layer
  python3 modules/code_graph.py --file modules/memory_refinement.py
"""

import argparse
import ast
import sqlite3
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
DB_PATH = SCRIPTS_DIR / ".hermes_code_graph.db"

EXCLUDE_DIR_PARTS = {"_backup", "__pycache__", ".venv", "venv", "node_modules", ".git"}


class _CodeVisitor(ast.NodeVisitor):
    """단일 파일 AST를 훑어서 함수/클래스/import/호출관계 추출."""

    def __init__(self):
        self.functions: list[tuple] = []   # (name, qualified_name, lineno, class_name, docstring)
        self.classes: list[tuple] = []     # (name, lineno, docstring)
        self.imports: list[tuple] = []     # (module, alias, lineno, is_from)
        self.calls: list[tuple] = []       # (caller_qualified, callee, lineno)
        self._func_stack: list[str] = []
        self._class_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        docstring = (ast.get_docstring(node) or "")[:200]
        self.classes.append((node.name, node.lineno, docstring))
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _visit_func(self, node):
        docstring = (ast.get_docstring(node) or "")[:200]
        class_name = self._class_stack[-1] if self._class_stack else None
        qualified = f"{class_name}.{node.name}" if class_name else node.name
        self.functions.append((node.name, qualified, node.lineno, class_name, docstring))
        self._func_stack.append(qualified)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._visit_func(node)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append((alias.name, alias.asname, node.lineno, False))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if module else alias.name
            self.imports.append((full, alias.asname, node.lineno, True))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        callee = None
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        if callee:
            caller = self._func_stack[-1] if self._func_stack else "<module>"
            self.calls.append((caller, callee, node.lineno))
        self.generic_visit(node)


def _iter_py_files() -> list[Path]:
    files = []
    for p in SCRIPTS_DIR.rglob("*.py"):
        if any(part in EXCLUDE_DIR_PARTS for part in p.parts):
            continue
        files.append(p)
    return files


def build_index(verbose: bool = True) -> dict:
    """Scripts/ 전체를 재인덱싱. 기존 DB는 통째로 갈아엎음(증분 아님 — 167개 파일 규모라 전체 재빌드로 충분)."""
    files = _iter_py_files()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.executescript("""
        DROP TABLE IF EXISTS functions;
        DROP TABLE IF EXISTS classes;
        DROP TABLE IF EXISTS imports;
        DROP TABLE IF EXISTS calls;
        CREATE TABLE functions (file TEXT, name TEXT, qualified_name TEXT, lineno INTEGER, class_name TEXT, docstring TEXT);
        CREATE TABLE classes (file TEXT, name TEXT, lineno INTEGER, docstring TEXT);
        CREATE TABLE imports (file TEXT, module TEXT, alias TEXT, lineno INTEGER, is_from INTEGER);
        CREATE TABLE calls (file TEXT, caller TEXT, callee TEXT, lineno INTEGER);
        CREATE INDEX idx_calls_callee ON calls(callee);
        CREATE INDEX idx_imports_module ON imports(module);
        CREATE INDEX idx_functions_name ON functions(name);
    """)

    parsed = 0
    failed = []
    for path in files:
        rel = str(path.relative_to(SCRIPTS_DIR))
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=rel)
        except SyntaxError as e:
            failed.append((rel, str(e)))
            continue

        visitor = _CodeVisitor()
        visitor.visit(tree)

        cur.executemany(
            "INSERT INTO functions VALUES (?,?,?,?,?,?)",
            [(rel, *f) for f in visitor.functions],
        )
        cur.executemany(
            "INSERT INTO classes VALUES (?,?,?,?)",
            [(rel, *c) for c in visitor.classes],
        )
        cur.executemany(
            "INSERT INTO imports VALUES (?,?,?,?,?)",
            [(rel, *i) for i in visitor.imports],
        )
        cur.executemany(
            "INSERT INTO calls VALUES (?,?,?,?)",
            [(rel, *c) for c in visitor.calls],
        )
        parsed += 1

    conn.commit()
    conn.close()

    result = {"files_indexed": parsed, "files_failed": len(failed), "failures": failed}
    if verbose:
        print(f"인덱싱 완료: {parsed}개 파일, 실패 {len(failed)}개")
        for rel, err in failed[:10]:
            print(f"  실패: {rel} — {err}")
    return result


def _ensure_index():
    if not DB_PATH.exists():
        print("인덱스 없음 — 최초 빌드 실행")
        build_index()


def find_callers(func_name: str) -> list[tuple]:
    """이 함수(또는 메서드명)를 호출하는 곳 전부."""
    _ensure_index()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT file, caller, lineno FROM calls WHERE callee = ? ORDER BY file, lineno",
        (func_name,),
    ).fetchall()
    conn.close()
    return rows


def find_importers(module_substr: str) -> list[tuple]:
    """이 모듈(부분 문자열 매칭)을 import 하는 파일 전부."""
    _ensure_index()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT file, module, lineno FROM imports WHERE module LIKE ? ORDER BY file",
        (f"%{module_substr}%",),
    ).fetchall()
    conn.close()
    return rows


def get_file_summary(rel_path: str) -> dict:
    """이 파일에 정의된 함수/클래스 + import 목록."""
    _ensure_index()
    conn = sqlite3.connect(DB_PATH)
    functions = conn.execute(
        "SELECT qualified_name, lineno, docstring FROM functions WHERE file = ? ORDER BY lineno",
        (rel_path,),
    ).fetchall()
    classes = conn.execute(
        "SELECT name, lineno, docstring FROM classes WHERE file = ? ORDER BY lineno",
        (rel_path,),
    ).fetchall()
    imports = conn.execute(
        "SELECT module, lineno FROM imports WHERE file = ? ORDER BY lineno",
        (rel_path,),
    ).fetchall()
    conn.close()
    return {"functions": functions, "classes": classes, "imports": imports}


def _main():
    parser = argparse.ArgumentParser(description="Hermes 경량 코드 지식그래프")
    parser.add_argument("--rebuild", action="store_true", help="Scripts/ 전체 재인덱싱")
    parser.add_argument("--callers", metavar="FUNC_NAME", help="이 함수를 호출하는 곳 조회")
    parser.add_argument("--importers", metavar="MODULE", help="이 모듈을 import하는 파일 조회")
    parser.add_argument("--file", metavar="REL_PATH", help="파일 요약 (함수/클래스/import)")
    args = parser.parse_args()

    if args.rebuild:
        build_index()
        return

    if args.callers:
        rows = find_callers(args.callers)
        if not rows:
            print(f"'{args.callers}' 호출하는 곳 없음")
        for file, caller, lineno in rows:
            print(f"  {file}:{lineno}  ({caller} 안에서 호출)")

    if args.importers:
        rows = find_importers(args.importers)
        if not rows:
            print(f"'{args.importers}' import하는 파일 없음")
        for file, module, lineno in rows:
            print(f"  {file}:{lineno}  import {module}")

    if args.file:
        summary = get_file_summary(args.file)
        print(f"=== {args.file} ===")
        print(f"클래스 {len(summary['classes'])}개:")
        for name, lineno, doc in summary["classes"]:
            print(f"  {name} (line {lineno}) — {doc[:60]}")
        print(f"함수 {len(summary['functions'])}개:")
        for qname, lineno, doc in summary["functions"]:
            print(f"  {qname} (line {lineno}) — {doc[:60]}")
        print(f"import {len(summary['imports'])}개:")
        for module, lineno in summary["imports"]:
            print(f"  line {lineno}: {module}")

    if not any([args.rebuild, args.callers, args.importers, args.file]):
        parser.print_help()


if __name__ == "__main__":
    _main()
