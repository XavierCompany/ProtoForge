#!/usr/bin/env python3
"""CI drift detector — validates documentation claims against actual code.

Run: python scripts/check_drift.py
Exit code 0 = all checks pass, 1 = drift detected.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERRORS: list[str] = []


def err(msg: str) -> None:
    ERRORS.append(msg)
    print(f"  FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"  OK:   {msg}")


# ---------------------------------------------------------------------------
# 1. Test count — collect pytest items, compare to copilot-instructions.md
# ---------------------------------------------------------------------------
def check_test_count() -> None:
    print("\n[1] Test count")
    # Prefer the project venv if it exists
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    result = subprocess.run(
        [python, "-m", "pytest", "--co", "-q"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    # Try "N tests collected" summary line first
    lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
    match = None
    for line in reversed(lines):
        match = re.search(r"(\d+)\s+tests?\s+collected", line)
        if match:
            break
    if match:
        actual = int(match.group(1))
    else:
        # Fall back: sum per-file counts (format: "tests/test_X.py: N")
        total = 0
        for line in lines:
            m = re.match(r".+\.py:\s*(\d+)$", line)
            if m:
                total += int(m.group(1))
        if total == 0:
            err(f"Could not parse pytest output: {lines[-1] if lines else '(empty)'}")
            return
        actual = total

    ci_path = ROOT / ".github" / "copilot-instructions.md"
    ci_text = ci_path.read_text(encoding="utf-8")
    m = re.search(r"test_\*\.py\s+#\s+(\d+)\s+tests", ci_text)
    if not m:
        err("copilot-instructions.md: cannot find test count pattern")
        return
    doc_count = int(m.group(1))
    if doc_count != actual:
        err(f"copilot-instructions.md says {doc_count} tests, actual {actual}")
    else:
        ok(f"Test count matches: {actual}")


# ---------------------------------------------------------------------------
# 2. Agent count — count AgentType enum members in router.py
# ---------------------------------------------------------------------------
def check_agent_count() -> None:
    print("\n[2] Agent count")
    router = (ROOT / "src" / "orchestrator" / "router.py").read_text(encoding="utf-8")
    # Match members like PLAN = "plan", CODE_RESEARCH = "code_research"
    members = re.findall(r"^\s+[A-Z_]+\s*=\s*\"", router, re.MULTILINE)
    actual = len(members)
    if actual < 1:
        err("Could not find AgentType enum members")
        return

    maint = (ROOT / "MAINTENANCE.md").read_text(encoding="utf-8")
    m = re.search(r"###\s+4\.3\s+Agents\s+\((\d+)\s+total\)", maint)
    if not m:
        err("MAINTENANCE.md: cannot find '4.3 Agents (N total)' header")
        return
    doc_count = int(m.group(1))
    if doc_count != actual:
        err(f"MAINTENANCE.md says {doc_count} agents, actual {actual}")
    else:
        ok(f"Agent count matches: {actual}")


# ---------------------------------------------------------------------------
# 3. Endpoint count — count @app decorators in server.py + server_routes/*.py
# ---------------------------------------------------------------------------
def check_endpoint_count() -> None:
    print("\n[3] Endpoint count")
    server_paths = [ROOT / "src" / "server.py", *sorted((ROOT / "src" / "server_routes").glob("*.py"))]
    actual = 0
    for path in server_paths:
        text = path.read_text(encoding="utf-8")
        decorators = re.findall(r"@app\.(get|post|put|delete|patch|websocket)", text)
        actual += len(decorators)

    ci_text = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+)\+?\s*endpoints", ci_text)
    if not m:
        err("copilot-instructions.md: cannot find endpoint count")
        return
    doc_count = int(m.group(1))
    if actual < doc_count:
        err(f"copilot-instructions.md says {doc_count}+ endpoints, actual {actual}")
    else:
        ok(f"Endpoint count: doc says {doc_count}+, actual {actual}")


# ---------------------------------------------------------------------------
# 4. Doc numbering — all *.md in root should say "of 10" (not "of 9")
# ---------------------------------------------------------------------------
def check_doc_numbering() -> None:
    print("\n[4] Doc numbering consistency")
    bad: list[str] = []
    for md in sorted(ROOT.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        if m := re.search(r"\*\*(\d+)\s+of\s+(\d+)\*\*", text):
            total = int(m.group(2))
            if total != 10:
                bad.append(f"{md.name}: says 'of {total}' not 'of 10'")
    ci = ROOT / ".github" / "copilot-instructions.md"
    ci_text = ci.read_text(encoding="utf-8")
    if m := re.search(r"\*\*(\d+)\s+of\s+(\d+)\*\*", ci_text):
        total = int(m.group(2))
        if total != 10:
            bad.append(f"copilot-instructions.md: says 'of {total}'")
    if bad:
        for b in bad:
            err(b)
    else:
        ok("All docs say 'of 10'")


# ---------------------------------------------------------------------------
# 5. src/llm/ present in copilot-instructions.md directory map
# ---------------------------------------------------------------------------
def check_llm_in_directory_map() -> None:
    print("\n[5] src/llm/ in copilot-instructions.md")
    ci_text = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    if "llm/" not in ci_text:
        err("copilot-instructions.md directory map is missing src/llm/")
    else:
        ok("src/llm/ is present in directory map")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print("=== ProtoForge Drift Check ===")
    check_test_count()
    check_agent_count()
    check_endpoint_count()
    check_doc_numbering()
    check_llm_in_directory_map()
    print()
    if ERRORS:
        print(f"DRIFT DETECTED — {len(ERRORS)} issue(s):")
        for e in ERRORS:
            print(f"  • {e}")
        return 1
    print("All checks passed — no drift detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
