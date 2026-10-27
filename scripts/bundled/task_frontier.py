#!/usr/bin/env python3
"""task_frontier.py — which open tasks can be started today, from `4-Trackers/tasks.md`.

    python .familiar/scripts/task_frontier.py            # from anywhere inside the project
    python .familiar/scripts/task_frontier.py --json

The frontier is every open task whose blockers are all cleared (`state-schema.md` → Tracker
conventions): a blocker is cleared when it is `none` / `nothing` / `—`, or names a task — by its ID
or its title — that is done. A blocker written in plain words ("client input", "PDUFA outcome") is
external and still blocks. `triage`, `ongoing-work`, and check-in used to work this out in the
model's head; this does the graph part and leaves effort, urgency, and priority to the model.

Reads `tasks.md` only and writes nothing. Standard library only. Output groups the frontier by the
`###` track heading each task sits under, and lists blocked tasks with what blocks them, plus any
blocker that names a task ID the file does not contain.
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import json
import re
import sys
from pathlib import Path

TASK = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$")
TASK_ID = re.compile(r"\*\*([A-Z]{1,5}-\d+)\*\*")
ID_ONLY = re.compile(r"^[A-Z]{1,5}-\d+$")
BLOCKED = re.compile(r"blocked by:\s*(.+)$", re.IGNORECASE)
NEXT_FIELD = re.compile(r"[;.,]?\s*\**\b(?:owner|due|follow[ -]?up|no date)\b\**:", re.IGNORECASE)
CLEAR = {"none", "nothing", "—", "-", "n/a", ""}
CLEARED_WITH_NOTE = re.compile(r"^(none|nothing|n/a)\b\s*[.,:(—-]", re.IGNORECASE)
# "**T13 — Title.** Owner: ..." keeps the ID and the title together in one bold span.
BOLD_LEAD = re.compile(r"^\*\*([A-Z]{1,5}-?\d+)\s+—\s+(.+?)\*\*")
SEPARATOR = re.compile(r"\s+—\s+")
PLACEHOLDER = "(example)"  # the old template's "- [ ] (example) — owner — due date" row
# "Owner: Sam." / "owner: Acme (Ana, R; Lee, A)" — up to the next field, sentence or separator.
OWNER = re.compile(r"\bowner:\**\s*((?:[^().;—|]|\([^)]*\))+?)\s*"
                   r"(?=\s—\s|\.\s|\.?\**\s*$|;|\||\s(?:due|follow up|no date|blocked by):)", re.IGNORECASE)
NO_OWNER = {"not set", "unassigned", "tbd", "none", "—", "-"}


def owner_in(text: str) -> str | None:
    m = OWNER.search(text)
    if not m:
        return None
    return "" if m.group(1).strip().lower() in NO_OWNER else m.group(1).strip()


def find_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".familiar-project").is_file():
            return folder
    return None


def title_of(body: str) -> str:
    lead = BOLD_LEAD.match(body.strip())
    if lead:
        return lead.group(2).strip(" *").strip()
    body = TASK_ID.sub("", body).strip(" —-*")
    return SEPARATOR.split(body, maxsplit=1)[0].strip(" *").strip()


def id_of(body: str) -> str | None:
    ident = TASK_ID.search(body) or BOLD_LEAD.match(body.strip())
    return ident.group(1) if ident else None


def title_ends(body: str) -> bool:
    """Whether `body` already holds the whole title. A title with nothing after it can wrap onto indented
    continuation lines, which belong to it until a ` — ` separator or a `Blocked by:` line."""
    return bool(BOLD_LEAD.match(body.strip()) or BLOCKED.search(body)
                or SEPARATOR.search(TASK_ID.sub("", body).strip(" —-*")))


def split_blockers(text: str) -> list[str]:
    # "Blocked by: T-01; Owner: Ben; Due: 2026-10-22" -- the blocker list ends at the next field.
    text = NEXT_FIELD.split(text, 1)[0]
    text = re.sub(r"\s+—\s+.*$", "", text.strip()).strip().rstrip(".")
    parts = [p.strip() for p in text.split(";")]
    out = []
    for part in parts:
        pieces = [q.strip() for q in part.split(",")]
        out.extend(pieces if all(ID_ONLY.match(q) for q in pieces) else [part])
    # "nothing. See `3-Knowledge/...` Gaps" is a cleared blocker followed by a note, not a blocker.
    return [p for p in out if p.lower() not in CLEAR and not CLEARED_WITH_NOTE.match(p)]


def parse(text: str) -> list[dict]:
    tasks, track, current, wrapping = [], "", None, None
    for line in text.splitlines():
        heading = re.match(r"^###\s+(.+?)\s*$", line)
        if heading:
            track, current = heading.group(1), None
            continue
        if re.match(r"^##\s", line):
            track, current = "", None
        m = TASK.match(line)
        if m:
            body = m.group(2)
            blocked = BLOCKED.search(body)
            current = {"id": id_of(body), "title": title_of(body),
                       "done": m.group(1).lower() == "x", "track": track, "owner": owner_in(body),
                       "blockers": split_blockers(blocked.group(1)) if blocked else []}
            wrapping = None if title_ends(body) else body
            if not current["title"].lower().startswith(PLACEHOLDER):
                tasks.append(current)
            continue
        if current is not None and line.startswith((" ", "\t")):
            if current["owner"] is None:
                current["owner"] = owner_in(line)
            blocked = BLOCKED.search(line)
            if blocked:
                current["blockers"] = split_blockers(blocked.group(1))
                wrapping = None
            elif wrapping is not None and re.match(r"^\s*(owner|due|follow up|no date)\b", line, re.IGNORECASE):
                wrapping = None  # a field line ends the title
            elif wrapping is not None and not re.match(r"^\s*[-*]\s", line):
                wrapping = wrapping.rstrip() + " " + line.strip()
                current["title"] = title_of(wrapping)
                if title_ends(wrapping):
                    wrapping = None
        elif line.strip():
            current = None
    return tasks


def frontier(tasks: list[dict]) -> dict:
    by_id = {t["id"]: t for t in tasks if t["id"]}
    by_title = {t["title"].lower(): t for t in tasks if t["title"]}
    workable, blocked, unknown = [], [], []
    for task in tasks:
        if task["done"]:
            continue
        open_blockers = []
        for ref in task["blockers"]:
            target = by_id.get(ref) or by_title.get(ref.lower())
            if target is None and ID_ONLY.match(ref):
                unknown.append(f"{task['id'] or task['title']} -> {ref}")
                open_blockers.append(f"{ref} (no such task)")
            elif target is None:
                open_blockers.append(f"{ref} (external)")
            elif not target["done"]:
                open_blockers.append(target["id"] or target["title"])
        entry = {"id": task["id"], "title": task["title"], "track": task["track"], "owner": task["owner"] or ""}
        if open_blockers:
            blocked.append({**entry, "blocked_by": open_blockers})
        else:
            workable.append(entry)
    return {"frontier": workable, "blocked": blocked, "unknown_ids": unknown}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_root(start.resolve())
    if root is None:
        raise SystemExit("ERROR: no .familiar-project found at or above " + str(start))
    path = root / "4-Trackers" / "tasks.md"
    if not path.is_file():
        raise SystemExit("ERROR: 4-Trackers/tasks.md does not exist")
    result = frontier(parse(path.read_text(encoding="utf-8", errors="replace")))
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"frontier: {len(result['frontier'])} task(s) can start now; {len(result['blocked'])} blocked")
    track = None
    for task in result["frontier"]:
        if task["track"] != track:
            track = task["track"]
            if track:
                print(f"  [{track}]")
        print(f"    {task['id'] + ' ' if task['id'] else ''}{task['title']}")
    if result["blocked"]:
        print("  blocked:")
        for task in result["blocked"]:
            print(f"    {task['id'] + ' ' if task['id'] else ''}{task['title'][:70]} <- {'; '.join(task['blocked_by'])}")
    for item in result["unknown_ids"]:
        print(f"  WARNING: blocker names a task that is not in tasks.md: {item}")


if __name__ == "__main__":
    main()
