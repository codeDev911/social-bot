"""
Keep the environment contract honest.

`.env.example` is the committed list of every variable the app understands; the
real values live in `.env` (gitignored) or in the host's secret store. This
script only compares KEY NAMES — it never reads values into the template.

    python scripts/env_keys.py             # .env vs .env.example
    python scripts/env_keys.py .env.prod   # any other env file
    python scripts/env_keys.py --audit     # code vs .env.example

`--audit` greps the codebase for os.getenv/os.environ lookups, so a newly added
variable cannot ship without being documented. Exit code 1 means drift.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".env.example"

# Lookups like os.getenv("X"), os.environ.get("X"), os.environ["X"].
# Variables resolved dynamically (COOKIES_<PLATFORM>_B64) are not visible here.
_CODE_PATTERN = re.compile(
    r"""os\.(?:getenv|environ\.get|environ\[)\s*\(?\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_SKIP_DIRS = (".venv", "__pycache__", "external_services", "site-packages")


def parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def code_vars() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if any(skip in relative for skip in _SKIP_DIRS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _CODE_PATTERN.finditer(text):
            found.setdefault(match.group(1), set()).add(relative)
    return found


def audit(template: dict[str, str]) -> int:
    used = code_vars()
    missing = sorted(set(used) - set(template))
    print(f"code reads {len(used)} variables, template declares {len(template)}")
    if not missing:
        print("ok: every variable read by the code is declared in .env.example")
        return 0
    print("missing from .env.example (add them with an empty value + comment):")
    for key in missing:
        print(f"  - {key}  <- {', '.join(sorted(used[key]))}")
    return 1


def main(argv: list[str]) -> int:
    template = parse(TEMPLATE)
    if not template:
        print(f"{TEMPLATE.name} is missing or empty")
        return 1

    args = argv[1:]
    if "--audit" in args:
        return audit(template)

    targets = [Path(arg) for arg in args] or [ROOT / ".env"]
    drifted = False

    for target in targets:
        actual = parse(target)
        print(f"{target.name}: {len(actual)} keys, template: {len(template)} keys")

        missing = sorted(set(template) - set(actual))
        unknown = sorted(set(actual) - set(template))

        if missing:
            # Expected: the template declares optional keys the runtime may omit.
            print("  not set here (optional, declared in .env.example):")
            for key in missing:
                print(f"    - {key}")
        if unknown:
            # Unexpected: a typo here is silently ignored by the app at runtime.
            drifted = True
            print("  unknown (set here, not declared in .env.example):")
            for key in unknown:
                print(f"    - {key}")
        if not missing and not unknown:
            print("  in sync")

    if drifted:
        print("Fix: declare the key in .env.example (empty value + comment) or correct the typo.")
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))