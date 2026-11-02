"""
Checks that every package code imports is listed in requirements.txt.

Why this matters: app runs on your PC because our venv has everything
installed. The Docker build only installs what requirements.txt lists, so one
missing line means a crash on the server.

Run it inside venv, in the project folder:

    python check_requirements.py
"""
import ast
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(".")

# Same things the Docker image leaves out (see .dockerignore): they are not
# needed on the server, so their imports (pytest, ...) are not checked.
SKIP_DIRS = {"venv", ".venv", "env", ".git", "__pycache__", ".pytest_cache",
             "node_modules", "site-packages", "tests", "eval", "scripts", "docs", "data"}
SKIP_FILES = {"main.py", "interactive_test.py"}

# Packages that only work on Windows and break a Linux Docker build.
WINDOWS_ONLY = {"pywin32", "pypiwin32", "pywinpty", "pywin32-ctypes"}

# Needed by the server but never imported by name in your own code.
SERVER_NEEDS = {
    "uvicorn": "runs the server",
    "python-multipart": "lets FastAPI read the intake upload form",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements(path: Path) -> dict[str, set[str]]:
    """name -> requested extras, from requirements.txt."""
    declared: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?", line)
        if not m:
            continue
        extras = {norm(e) for e in (m.group(2) or "").split(",") if e.strip()}
        declared.setdefault(norm(m.group(1)), set()).update(extras)
    return declared


def expand(declared: dict[str, set[str]]) -> set[str]:
    """Everything requirements.txt installs, including dependencies of dependencies."""
    seen: set[str] = set()
    queue = list(declared.items())
    while queue:
        name, extras = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            requires = metadata.distribution(name).requires or []
        except metadata.PackageNotFoundError:
            continue
        for req in requires:
            spec, _, marker = req.partition(";")
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?", spec)
            if not m:
                continue
            wanted_extras = set(re.findall(r"extra\s*==\s*['\"]([^'\"]+)['\"]", marker))
            if wanted_extras and not ({norm(e) for e in wanted_extras} & extras):
                continue  # an optional extra nobody asked for
            sub_extras = {norm(e) for e in (m.group(2) or "").split(",") if e.strip()}
            queue.append((norm(m.group(1)), sub_extras))
    return seen


def project_files() -> list[Path]:
    files = []
    for p in ROOT.rglob("*.py"):
        parts = set(p.relative_to(ROOT).parts[:-1])
        if parts & SKIP_DIRS:
            continue
        if p.parent == ROOT and (p.name in SKIP_FILES or p.name.startswith(("test_", "check_"))):
            continue
        files.append(p)
    return files


def local_module_names() -> set[str]:
    names = {p.stem for p in ROOT.glob("*.py")}
    for d in ROOT.iterdir():
        if d.is_dir() and d.name not in SKIP_DIRS and any(d.rglob("*.py")):
            names.add(d.name)
    return names


def imported_modules(files: list[Path]) -> set[str]:
    modules: set[str] = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            print(f"warning: could not read {f} ({e.msg}), skipped")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


def main() -> int:
    req_path = ROOT / "requirements.txt"
    if not req_path.exists():
        print("requirements.txt not found. Run this in your project folder.")
        return 1

    declared = parse_requirements(req_path)
    installed_by_requirements = expand(declared)

    third_party = {
        m for m in imported_modules(project_files())
        if m not in sys.stdlib_module_names and m not in local_module_names() and m != "__future__"
    }

    owners = metadata.packages_distributions()
    missing: dict[str, str] = {}
    unknown: list[str] = []
    for module in sorted(third_party):
        dists = owners.get(module)
        if not dists:
            unknown.append(module)
            continue
        if not any(norm(d) in installed_by_requirements for d in dists):
            dist = dists[0]
            missing[dist] = f"{dist}=={metadata.version(dist)}"

    problems = 0
    if missing:
        problems += 1
        print("MISSING from requirements.txt (your code imports them). Add these lines:")
        for line in missing.values():
            print(f"    {line}")
        print()
    if unknown:
        print("Imported, but not installed in this venv (typo, or an optional import?):")
        for m in unknown:
            print(f"    {m}")
        print()

    for name, why in SERVER_NEEDS.items():
        if name not in installed_by_requirements:
            problems += 1
            print(f"MISSING: {name} ({why}). Add the line: {name}")

    if "psycopg" in installed_by_requirements and not (
        "binary" in declared.get("psycopg", set()) or "psycopg-binary" in installed_by_requirements
    ):
        problems += 1
        print("PROBLEM: psycopg needs the binary extra inside Docker. Use: psycopg[binary,pool]")

    windows_only = sorted(WINDOWS_ONLY & set(declared))
    if windows_only:
        problems += 1
        print(f"PROBLEM: {', '.join(windows_only)} only work on Windows and break the Docker build. Remove them.")

    unpinned = sorted(n for n in declared if not re.search(
        rf"^{re.escape(n)}\b.*(==|~=|>=)", req_path.read_text(errors="replace"), re.I | re.M))
    if unpinned:
        print("Note: no version given for " + ", ".join(unpinned[:8])
              + (" ..." if len(unpinned) > 8 else "")
              + ". It works, but a future release could change behaviour. Pin them when you can.")

    if problems == 0:
        print("OK: requirements.txt covers everything your code imports.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())