#!/usr/bin/env python3
"""Registry drift audit for the CFOS skill bundle.

Invariants:

1. Every skill named in `skills/cfos-router/SKILL.md` exists as a sibling
   skill directory. The router cannot route to a skill that is not installed.
2. Every installed sibling skill appears in at least one registry row. The
   router has no blind spots.
3. Every SKILL.md frontmatter `name` matches its directory name.

Exit codes: 0 = in sync, 1 = drift, 2 = parse/setup error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

START = "<!-- registry:start -->"
END = "<!-- registry:end -->"
NAME_RE = re.compile(r"^name:\s*([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


def registry_names(text: str) -> list[str]:
    if START not in text or END not in text:
        raise ValueError(f"missing {START!r} / {END!r} markers")
    block = text.split(START, 1)[1].split(END, 1)[0]
    names: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = line.strip("|").split("|")
        if not cells:
            continue
        first = cells[0].strip()
        if first.startswith("`") and first.endswith("`"):
            name = first.strip("`")
            if name and all(c.isalnum() or c == "-" for c in name):
                names.append(name)
    return names


def frontmatter_name(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = NAME_RE.search(text)
    return match.group(1) if match else None


def installed_names(skills_dir: Path) -> set[str]:
    return {p.parent.name for p in skills_dir.glob("*/SKILL.md")}


def main() -> int:
    here = Path(__file__).resolve()
    default_skill = here.parents[1] / "SKILL.md"
    default_dir = here.parents[1].parent

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-md", type=Path, default=default_skill)
    parser.add_argument("--skills-dir", type=Path, default=default_dir)
    args = parser.parse_args()

    if not args.skill_md.is_file():
        print(f"ERROR: router skill not found: {args.skill_md}", file=sys.stderr)
        return 2
    if not args.skills_dir.is_dir():
        print(f"ERROR: skills dir not found: {args.skills_dir}", file=sys.stderr)
        return 2

    try:
        routed = registry_names(args.skill_md.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    installed = installed_names(args.skills_dir)
    routed_set = set(routed)

    missing = sorted(routed_set - installed)
    unlisted = sorted(installed - routed_set)

    bad_names: list[str] = []
    for name in sorted(installed):
        path = args.skills_dir / name / "SKILL.md"
        declared = frontmatter_name(path)
        if declared != name:
            bad_names.append(f"{name}: frontmatter name={declared!r}")

    duplicates = sorted({n for n in routed if routed.count(n) > 1})

    print(f"routed={len(routed_set)} installed={len(installed)}")
    if duplicates:
        print(f"INFO duplicate registry rows: {', '.join(duplicates)}")
    if missing:
        print("DRIFT routed but not installed: " + ", ".join(missing))
    if unlisted:
        print("DRIFT installed but not routed: " + ", ".join(unlisted))
    if bad_names:
        print("DRIFT frontmatter mismatch: " + "; ".join(bad_names))

    if missing or unlisted or bad_names:
        print("RESULT: DRIFT")
        return 1
    print("RESULT: IN SYNC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
