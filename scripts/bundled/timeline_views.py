#!/usr/bin/env python3
"""timeline_views.py — generate read-only schedule views from `4-Trackers/timeline.md`.

    python .familiar/scripts/timeline_views.py              # write 4-Trackers/_views/*.md
    python .familiar/scripts/timeline_views.py --stdout     # print, write nothing
    python .familiar/scripts/timeline_views.py --json       # machine-readable, write nothing
    python .familiar/scripts/timeline_views.py --json --window 42   # JSON "upcoming" reaches 42 days
    python .familiar/scripts/timeline_views.py --today 2026-09-26

`timeline.md` is the only schedule anyone edits. This script renders two views from it into
`4-Trackers/_views/` and writes nothing else:

  next-two-weeks.md   open milestones that are overdue or due in the next 14 days, plus
                      dependency problems and rows whose dates cannot be read
  gantt.md            a Mermaid gantt chart of every dated milestone

Each view opens with a stamp naming the timeline revision it was built from. When `timeline.md`
changes, `project_check.py` sees the old stamp and reports the views stale; rerun this script
(check-in and the date-change propagation in `calendar-rules.md` do). A person never edits a view
— there is exactly one editable plan, which is the point (2026-09-23 audit, finding 1).

It reports dependency problems but never moves a date: a slipped predecessor does not move a fixed
client or agency deadline, it costs slack, and deciding what to do about that is the user's call.

Reads the current columns (`state-schema.md` → Tracker conventions) and the older
`Milestone | Date | Type | Status | Depends on` layout, so a project works before its rows are
converted. Only full ISO dates are used; anything else is listed as unreadable, never guessed.
Standard library only.
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
from datetime import date, timedelta
from pathlib import Path

ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
REVISION = re.compile(r"<!--\s*revision:\s*(\d+)\s*-->")
CLOSED = ("done", "not applicable", "n/a", "cancelled", "canceled", "complete", "completed")
AT_RISK = ("at risk", "slipped", "off track")
STAMP = "<!-- generated from timeline.md revision {rev} on {today} by .familiar/scripts/timeline_views.py; do not edit -- edit timeline.md and regenerate -->"
STAMP_RE = re.compile(r"<!-- generated from timeline\.md revision (\d+) on (\d{4}-\d{2}-\d{2})")
WINDOW_DAYS = 14
# A start or a cadence is not a deadline: "drafting from 2026-08-25", "2026-09-09, then bi-weekly".
NOT_A_DEADLINE = re.compile(r"\b(from|starts?|started|since|recurring|weekly|bi-weekly|monthly|then)\b", re.IGNORECASE)


def find_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".familiar-project").is_file():
            return folder
    return None


def iso_dates(text: str) -> list[date]:
    found = []
    for y, m, d in ISO.findall(text or ""):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found


def parse(text: str) -> list[dict]:
    """Milestone rows from the first table with a Due or Date column and a Status column.
    Each row gets id, name, track, start, due, confirmed, type, status, depends (list), owner,
    and `dated` (False when no full ISO date could be read)."""
    header, rows = None, []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if header is not None and rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            lowered = [c.lower() for c in cells]
            # "Due" (current layout), or any date-named column in older ones ("Date", "Target Date").
            if ("due" in lowered or any("date" in h for h in lowered)) and "status" in lowered:
                header = lowered
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        name = row.get("milestone", "")
        if re.match(r"^\W*\(example\)", name, re.IGNORECASE):
            continue
        when = row.get("due") or next((v for k, v in row.items() if "date" in k), "")
        dates = [] if NOT_A_DEADLINE.search(when) else iso_dates(when)
        depends = [d.strip() for d in re.split(r"[,;]", row.get("depends on", "")) if d.strip() not in ("", "—", "-")]
        rows.append({
            "id": row.get("id") or f"row {len(rows) + 1}",
            "has_id": bool(row.get("id")),
            "name": name,
            "track": row.get("track", ""),
            "when": when,
            "start": min(dates) if len(dates) > 1 else None,
            "due": max(dates) if dates else None,
            "dated": bool(dates) and not when.lower().startswith("no date"),
            "no_date": when.lower().startswith("no date"),
            "confirmed": row.get("confirmed", ""),
            "type": row.get("type", ""),
            "status": row.get("status", ""),
            "depends": depends,
            "owner": row.get("owner", ""),
        })
    return rows


def is_open(row: dict) -> bool:
    return not row["status"].lower().startswith(CLOSED)


def dependency_problems(rows: list[dict]) -> list[str]:
    """Plain-language problems, never fixes. A dependency named by ID resolves by ID; an older
    layout that names its dependency by milestone text resolves by name."""
    by_key = {}
    for row in rows:
        by_key[row["id"].lower()] = row
        by_key[row["name"].lower()] = row
    problems, unresolved_text = [], 0

    def resolve(ref):
        dep = by_key.get(ref.lower())
        if dep is not None:
            return dep
        # Older layouts name a dependency in shorthand ("client materials" for "client shares ...
        # materials"); accept one unambiguous milestone whose name contains every word of it.
        words = [w for w in re.findall(r"\w+", ref.lower()) if len(w) > 2]
        hits = [r for r in rows if words and all(w in r["name"].lower() for w in words)]
        return hits[0] if len(hits) == 1 else None

    for row in rows:
        if not is_open(row):
            continue
        for ref in row["depends"]:
            dep = resolve(ref)
            if dep is None:
                if row["has_id"] and re.match(r"^M-\d+$", ref, re.IGNORECASE):
                    problems.append(f"{row['id']} ({row['name']}) depends on {ref}, which is not a milestone "
                                    "in this timeline.")
                else:
                    unresolved_text += 1
                continue
            if dep["due"] and row["due"] and is_open(dep) and dep["due"] > row["due"]:
                fixed = " It is a fixed date: decide what gives, do not just move it." \
                    if "hard external" in row["type"].lower() or "client" in row["type"].lower() else ""
                problems.append(f"{row['id']} ({row['name']}, due {row['due']}) depends on {dep['id']} "
                                f"({dep['name']}), due later on {dep['due']}.{fixed}")
            elif dep["status"].lower().startswith(AT_RISK):
                problems.append(f"{row['id']} ({row['name']}) depends on {dep['id']} ({dep['name']}), "
                                f"which is {dep['status']}; check the slack before its date.")
    if unresolved_text:
        problems.append(f"{unresolved_text} dependency reference(s) are written as text that matches no single "
                        "milestone, so they cannot be checked. Give milestones IDs (M-01, M-02) and list "
                        "dependencies by ID.")
    return problems


def next_two_weeks(rows, rev, today) -> str:
    horizon = today + timedelta(days=WINDOW_DAYS)
    overdue = [r for r in rows if is_open(r) and r["dated"] and r["due"] < today]
    upcoming = [r for r in rows if is_open(r) and r["dated"] and today <= r["due"] <= horizon]
    unreadable = [r for r in rows if is_open(r) and not r["dated"] and not r["no_date"]]
    # A row with only a start date or a cadence is not late; it needs its own deadline or none.
    out = [STAMP.format(rev=rev, today=today), f"# Next two weeks — {today} to {horizon}", ""]

    def line(r):
        conf = r["confirmed"] or "confirmation not recorded"
        owner = f" — {r['owner']}" if r["owner"] else ""
        return f"- **{r['id']}** {r['name']} — {r['when']} — {r['status'] or 'status not set'} ({conf}){owner}"

    out += ["## Overdue and still open", ""] + ([line(r) for r in sorted(overdue, key=lambda r: r["due"])] or ["None."])
    out += ["", "## Due in the next 14 days", ""] + ([line(r) for r in sorted(upcoming, key=lambda r: r["due"])] or ["None."])
    problems = dependency_problems(rows)
    out += ["", "## Dependency problems", ""] + ([f"- {p}" for p in problems] or ["None found."])
    if unreadable:
        out += ["", "## Dates this view cannot read", "",
                "Convert these rows to a full date, a date window, or `No date: <reason>` at the next check-in.", ""]
        out += [f"- **{r['id']}** {r['name']} — `{r['when'] or 'blank'}`" for r in unreadable]
    return "\n".join(out) + "\n"


def as_json(rows, rev, today, window=WINDOW_DAYS) -> dict:
    """The same overdue / upcoming / problem lists the next-two-weeks view prints, as data. `window`
    widens "upcoming" for callers that look further ahead (the hub's brief); the views stay at 14 days."""
    horizon = today + timedelta(days=window)

    def item(r):
        return {"id": r["id"], "name": r["name"], "track": r["track"], "when": r["when"],
                "due": r["due"].isoformat() if r["due"] else None, "status": r["status"],
                "confirmed": r["confirmed"], "owner": r["owner"], "type": r["type"]}

    open_rows = [r for r in rows if is_open(r)]
    return {
        "today": today.isoformat(), "revision": rev, "window": window, "milestones": len(rows),
        "dated": sum(1 for r in rows if r["dated"]),
        "overdue": [item(r) for r in sorted((r for r in open_rows if r["dated"] and r["due"] < today),
                                            key=lambda r: r["due"])],
        "upcoming": [item(r) for r in sorted((r for r in open_rows if r["dated"] and today <= r["due"] <= horizon),
                                             key=lambda r: r["due"])],
        "unreadable": [item(r) for r in open_rows if not r["dated"] and not r["no_date"]],
        "dependency_problems": dependency_problems(rows),
    }


def mermaid_id(row) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", row["id"])


def gantt(rows, rev, today) -> str:
    out = [STAMP.format(rev=rev, today=today), "# Timeline gantt", "",
           "Rendered from `timeline.md`. Milestones without a full date are not drawn.", "",
           "```mermaid", "gantt", "    dateFormat YYYY-MM-DD", "    title Milestones", f"    todayMarker on"]
    dated = [r for r in rows if r["dated"]]
    tracks = []
    for r in dated:
        if r["track"] not in tracks:
            tracks.append(r["track"])
    for track in tracks:
        out.append(f"    section {track or 'Milestones'}")
        for r in dated:
            if r["track"] != track:
                continue
            status = r["status"].lower()
            tags = []
            if status.startswith(CLOSED):
                tags.append("done")
            elif status.startswith(AT_RISK):
                tags.append("crit")
            elif status.startswith("in progress"):
                tags.append("active")
            label = re.sub(r"[:#;]", " ", r["name"])[:60].strip() or r["id"]
            if r["start"]:
                tags.append(mermaid_id(r))
                out.append(f"    {label} :{', '.join(tags)}, {r['start']}, {r['due']}")
            else:
                tags = ["milestone"] + tags + [mermaid_id(r)]
                out.append(f"    {label} :{', '.join(tags)}, {r['due']}, 0d")
    out += ["```", ""]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stdout", action="store_true", help="print the views instead of writing them")
    ap.add_argument("--json", action="store_true", help="print the schedule summary as JSON, write nothing")
    ap.add_argument("--today", help="YYYY-MM-DD, for testing")
    ap.add_argument("--root", help="project folder; defaults to the nearest parent with .familiar-project")
    ap.add_argument("--window", type=int, default=WINDOW_DAYS,
                    help=f"with --json: days ahead that count as upcoming (default {WINDOW_DAYS})")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_root(start.resolve())
    if root is None:
        raise SystemExit("ERROR: no .familiar-project found at or above " + str(start))
    timeline = root / "4-Trackers" / "timeline.md"
    if not timeline.is_file():
        raise SystemExit("ERROR: 4-Trackers/timeline.md does not exist")
    text = timeline.read_text(encoding="utf-8")
    rev_match = REVISION.search(text)
    rev = rev_match.group(1) if rev_match else "unknown"
    today = date.fromisoformat(args.today) if args.today else date.today()
    rows = parse(text)
    if args.json:
        print(json.dumps({"root": str(root), **as_json(rows, rev, today, max(args.window, 0))}, indent=2))
        return
    views = {"next-two-weeks.md": next_two_weeks(rows, rev, today), "gantt.md": gantt(rows, rev, today)}
    if args.stdout:
        for name, body in views.items():
            print(f"===== {name}\n{body}")
        return
    folder = root / "4-Trackers" / "_views"
    folder.mkdir(exist_ok=True)
    for name, body in views.items():
        (folder / name).write_text(body, encoding="utf-8", newline="\n")
    dated = sum(1 for r in rows if r["dated"])
    print(f"views written to 4-Trackers/_views/ from timeline.md revision {rev}: "
          f"{len(rows)} milestone(s), {dated} with a readable date, "
          f"{len(dependency_problems(rows))} dependency problem(s)")


if __name__ == "__main__":
    main()
