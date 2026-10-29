#!/usr/bin/env python3
"""project_check.py — read-only health check of one familiar project's state files.

    python .familiar/scripts/project_check.py            # from anywhere inside the project
    python .familiar/scripts/project_check.py --json     # machine-readable
    python .familiar/scripts/project_check.py --today 2026-09-26

Called by `/project-status` → Check-in (check-in.md → 4. Health check). It reads and reports; it never writes, moves,
or fixes anything, and it prints only exceptions — a clean project prints one line. The model
interprets the findings and asks before changing anything. If this script cannot run on a
machine, check-in says so and does the same checks by reading the files, so a blocked script
degrades the check instead of silently skipping it.

Standard library only. It stays inside the project that contains it (CORE-WORKSPACE-SILO): the
project root is the nearest parent holding `.familiar-project`.

Not the same as `doctor.py` beside it, which checks this machine's document toolchain.

Date handling is deliberately conservative. Only ISO dates (YYYY-MM-DD) are compared. A date
written any other way ("2026-10", "Week of Oct 5", "D1 2026-11-02–06") is reported as not
machine-readable rather than guessed, because a wrong "overdue" is worse than an honest "can't tell".
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
LAST_UPDATED = re.compile(r"<!--\s*last-updated:\s*([^\s>]+)\s*-->")
REVISION = re.compile(r"<!--\s*revision:\s*(\d+)\s*-->")
OPEN_TASK = re.compile(r"^\s*[-*]\s+\[ \]\s+(.*)$")
OWNER = re.compile(r"\bowner:\s*([^—;|\n]+)", re.IGNORECASE)
# "Due: 2026-10-02" or "due: not set"; the colon-less form counts only when a full date follows,
# so prose like "due to the client" is never read as a date.
DUE = re.compile(r"\bdue(?::\s*([^—;|\n]+)|\s+(\d{4}-\d{2}-\d{2}))", re.IGNORECASE)
# Some projects end a task with " — <name>" instead of an owner: field.
TRAILING_OWNER = re.compile(
    r"—\s*([A-Z][\w'.-]*(?:\s*(?:[+&/,]|and)?\s*[A-Z][\w'.-]*){0,3}(?:\s*\([^)]*\))?)\s*$")
# Template rows a project never deleted ("(example) — owner — due date") are not real work.
EXAMPLE = re.compile(r"^\W*\(example\)", re.IGNORECASE)
# A date that starts or repeats something is not a deadline: "drafting from 2026-08-25",
# "2026-09-09 3:00pm, then bi-weekly".
NOT_A_DEADLINE = re.compile(r"\b(from|starts?|started|since|recurring|weekly|bi-weekly|monthly|then)\b",
                            re.IGNORECASE)
FOLLOW_UP = re.compile(r"\bfollow[ -]?up:\s*([^—;|\n]+)", re.IGNORECASE)
NO_DATE = re.compile(r"\bno date:\s*\S", re.IGNORECASE)
LIFECYCLE = re.compile(r"\*\*Lifecycle:\*\*\s*(active|paused|reference|closed)\b", re.IGNORECASE)
CONFIRMED = re.compile(r"\bconfirmed\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
TASK_ID = re.compile(r"\*\*(T-\d+)\*\*")
LOG_HEADER = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]", re.MULTILINE)
CONFLICT_COPY = re.compile(r"conflicted copy|-DESKTOP-|-LAPTOP-|\(\d+\)\.md$", re.IGNORECASE)
NOT_SET = {"not set", "none", "n/a", "tbd", "unknown", ""}
CLOSED_STATUS = ("done", "not applicable", "n/a", "cancelled", "canceled", "complete", "completed")
HASH_LIMIT = 150 * 1024 * 1024
STALE_SNAPSHOT_DAYS = 14
SNAPSHOT_LINE_LIMIT = 60
SNAPSHOT_LINE_CHARS = 250
STALE_HANDOFF_DAYS = 7
HANDOFF_STATUS = re.compile(r"^\*\*Status:\*\*\s*(open|resumed)\b(.*)$", re.IGNORECASE | re.MULTILINE)
HANDOFF_WRITTEN = re.compile(r"^\*\*Written:\*\*\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)
HANDOFF_COVERS = re.compile(r"^\*\*Covers:\*\*\s*(.+)$", re.MULTILINE)
DRAFT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_(?P<slug>.+)_(DRAFT|REVIEW|FINAL)_v(?P<n>\d+)(_AI|_[A-Z]{2,4})?[._]")
RETIRED_FLAT_RULE = re.compile(r"Keep\s+files\s+flat\s+while", re.I)
DRAFT_SKIP_DIRS = {"_superseded", "_del", "_build"}
DRAFT_SKIP_FILES = {"_about.md", "_draft-index.md", "_versions.md", "_moves.md", "_brief.md"}
DRAFT_JUNK_DIRS = {"__pycache__", "node_modules"}
DRAFT_JUNK_EXT = {".py", ".js", ".ps1", ".sh", ".pyc"}
INDEX_CELL_LIMIT = 120
INDEX_PATH_LIMIT = 200
INDEX_BYTES_LIMIT = 20 * 1024
SCRATCH_BYTES_LIMIT = 50 * 1024 * 1024
SCRATCH_AGE_DAYS = 14
TOP_LEVEL_OK = re.compile(r"^(\d-.*|_del|_backups|_update|\.(agents|claude|codex|gemini|familiar|scratch|git|github|vscode|idea|obsidian))$")
TOP_LEVEL_TMP = re.compile(r"(tmp|temp|scratch|cache)", re.IGNORECASE)


def find_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".familiar-project").is_file():
            return folder
    return None


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def iso_dates(text: str) -> list[date]:
    found = []
    for y, m, d in ISO.findall(text):
        try:
            found.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return found


def last_updated(text: str) -> date | None:
    m = LAST_UPDATED.search(text)
    if not m:
        return None
    dates = iso_dates(m.group(1))
    return dates[0] if dates else None


def is_stub(text: str) -> bool:
    rev = REVISION.search(text)
    return bool(rev and rev.group(1) == "0") or "NOT YET WRITTEN" in text


class Report:
    """Collects findings. A project whose snapshot `Lifecycle` is not `active` (paused, reference,
    closed) still gets every finding, but schedule and ownership ones are reported as information,
    not as problems -- a paused personal project should not raise overdue noise."""

    QUIETED_WHEN_INACTIVE = {"status", "tasks", "timeline", "meetings"}

    def __init__(self, lifecycle: str = "active"):
        self.findings = []
        self.lifecycle = lifecycle

    def add(self, area: str, level: str, message: str):
        if self.lifecycle != "active" and level == "attention" and area in self.QUIETED_WHEN_INACTIVE:
            level, message = "info", f"(project is {self.lifecycle}) {message}"
        self.findings.append({"area": area, "level": level, "message": message})


def snapshot_lifecycle(root: Path) -> str:
    m = LIFECYCLE.search(read(root / "5-System" / "Memory" / "status-snapshot.md"))
    return m.group(1).lower() if m else "active"


def check_setup(root: Path, r: Report):
    marker = read(root / ".familiar-project")
    config_path = root / "5-System" / "project-config.md"
    config = read(config_path)
    unset = "created: not-yet-set" in marker
    if unset and config and not is_stub(config):
        r.add("setup", "attention", "`.familiar-project` still reads `created: not-yet-set` but "
              "`5-System/project-config.md` is filled in. Check-in will reconcile it, not reset it; "
              "stamping `created` makes the marker truthful.")
    if config and is_stub(config):
        populated = [p for p in ("5-System/Memory/status-snapshot.md", "4-Trackers/tasks.md",
                                 "4-Trackers/timeline.md") if (root / p).is_file() and not is_stub(read(root / p))]
        if populated:
            r.add("setup", "attention", "`5-System/project-config.md` was never written although "
                  f"{', '.join('`' + p + '`' for p in populated)} hold real state.")
    if not config_path.is_file():
        r.add("setup", "attention", "`5-System/project-config.md` is missing.")


def check_snapshot(root: Path, today: date, r: Report):
    snap_path = root / "5-System" / "Memory" / "status-snapshot.md"
    snap = read(snap_path)
    if not snap:
        r.add("status", "attention", "`5-System/Memory/status-snapshot.md` is missing or unreadable.")
        return
    if is_stub(snap):
        r.add("status", "info", "The status snapshot has never been written (still the template).")
        return
    body = [l for l in snap.splitlines() if l.strip() and not l.lstrip().startswith("<!--")]
    long_lines = [l for l in body if len(l) > SNAPSHOT_LINE_CHARS]
    if len(body) > SNAPSHOT_LINE_LIMIT or long_lines:
        parts = []
        if len(body) > SNAPSHOT_LINE_LIMIT:
            parts.append(f"{len(body)} lines (ceiling {SNAPSHOT_LINE_LIMIT})")
        if long_lines:
            parts.append(f"{len(long_lines)} line(s) over {SNAPSHOT_LINE_CHARS} characters, the longest "
                         f"{max(len(l) for l in long_lines)}")
        r.add("status", "attention", "The status snapshot is over its ceiling: " + "; ".join(parts)
              + ". Move the detail to the log and keep one short line per bullet.")
    if "**Current work:**" not in snap:
        r.add("status", "info", "The snapshot has no `Current work` lines, so nothing names the current file "
              "of each piece of work. `/save progress` adds them.")
    updated = last_updated(snap)
    if updated is None:
        r.add("status", "info", "The status snapshot has no machine-readable `last-updated` date.")
    else:
        age = (today - updated).days
        if age > STALE_SNAPSHOT_DAYS:
            r.add("status", "attention", f"The status snapshot was last updated {updated} ({age} days "
                  "ago). Treat its health rating as unconfirmed until a check-in refreshes it.")
    # Only the status line carries the confirmation; "Sam confirmed 2026-08-13 the fix held" in a
    # bullet is history, not the status's confirmation date.
    status_line = next((l for l in snap.splitlines() if "**Status" in l), "")
    confirmed = CONFIRMED.search(status_line)
    if confirmed is None:
        r.add("status", "info", "The snapshot's status carries no `confirmed` date, so nothing shows "
              "when the user last agreed with it.")
    else:
        when = iso_dates(confirmed.group(1))
        if when and (today - when[0]).days > STALE_SNAPSHOT_DAYS:
            r.add("status", "attention", f"The status was last confirmed {when[0]} "
                  f"({(today - when[0]).days} days ago); report it as unconfirmed.")
    log = read(root / "5-System" / "Memory" / "status-log.md")
    entries = [d for d in iso_dates(" ".join(LOG_HEADER.findall(log)))]
    if entries and updated and max(entries) > updated:
        r.add("status", "attention", f"The status log has an entry dated {max(entries)}, newer than "
              f"the snapshot ({updated}); the snapshot may not reflect it.")


def check_tasks(root: Path, today: date, r: Report):
    text = read(root / "4-Trackers" / "tasks.md")
    if not text or is_stub(text):
        return
    no_owner, no_date, overdue, unreadable = [], [], [], []
    for line in text.splitlines():
        m = OPEN_TASK.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        if EXAMPLE.match(body):
            continue
        label = (TASK_ID.search(body).group(1) if TASK_ID.search(body) else body[:70].rstrip())
        owner = OWNER.search(body)
        owner_value = owner.group(1).strip().lower() if owner else None
        if owner_value is None:
            if not TRAILING_OWNER.search(body):
                no_owner.append(label)
        elif owner_value in NOT_SET:
            no_owner.append(label)
        due_match = DUE.search(body)
        due_text = (due_match.group(1) or due_match.group(2)).strip() if due_match else None
        if due_text is None or due_text.lower() in NOT_SET:
            follow = FOLLOW_UP.search(body)
            if follow and follow.group(1).strip().lower() not in NOT_SET:
                dates = iso_dates(follow.group(1))
                if not dates:
                    unreadable.append(f"{label} (follow up: {follow.group(1).strip()[:30]})")
                elif max(dates) < today:
                    overdue.append(f"{label} (follow-up was {max(dates)})")
            elif not NO_DATE.search(body):
                no_date.append(label)
            continue
        dates = iso_dates(due_text)
        if not dates:
            unreadable.append(f"{label} (due: {due_text[:30]})")
        elif max(dates) < today:
            overdue.append(f"{label} (due {max(dates)})")
    if overdue:
        r.add("tasks", "attention", f"{len(overdue)} open task(s) past their due or follow-up date: " + "; ".join(overdue[:10])
              + (" …" if len(overdue) > 10 else ""))
    if no_owner:
        r.add("tasks", "attention", f"{len(no_owner)} open task(s) with no owner: " + "; ".join(no_owner[:10])
              + (" …" if len(no_owner) > 10 else ""))
    if no_date:
        r.add("tasks", "info", f"{len(no_date)} open task(s) with no due or follow-up date and no stated "
              "reason: " + "; ".join(no_date[:10]) + (" …" if len(no_date) > 10 else ""))
    if unreadable:
        r.add("tasks", "info", f"{len(unreadable)} due date(s) not machine-readable: " + "; ".join(unreadable[:5]))


def milestone_rows(text: str):
    """Yield (cells, header) for each row of the first table carrying Date and Status columns."""
    header = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            if header is not None:
                return
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            lowered = [c.lower() for c in cells]
            if ("due" in lowered or any("date" in h for h in lowered)) and "status" in lowered:
                header = lowered
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        yield dict(zip(header, cells))


def check_timeline(root: Path, today: date, r: Report):
    timeline_path = root / "4-Trackers" / "timeline.md"
    text = read(timeline_path)
    if not text or is_stub(text):
        if (root / "4-Trackers" / "tasks.md").is_file() and not is_stub(read(root / "4-Trackers" / "tasks.md")):
            r.add("timeline", "info", "No milestones recorded. If the project has commitments with dates, "
                  "the timeline has not been established; if it has none, say so in the snapshot.")
        return
    past, unreadable = [], []
    for row in milestone_rows(text):
        status = row.get("status", "").lower()
        if any(status.startswith(s) for s in CLOSED_STATUS):
            continue
        name = (row.get("milestone") or next(iter(row.values()), ""))[:60]
        if EXAMPLE.match(name):
            continue
        when = row.get("due") or next((v for k, v in row.items() if "date" in k), "")
        # Only the date cell decides whether a date is a deadline, as in timeline_views.py: a
        # milestone named "Feedback from the client" is still a deadline.
        if NOT_A_DEADLINE.search(when):
            continue
        dates = iso_dates(when)
        if not dates:
            unreadable.append(name)
        elif max(dates) < today:
            past.append(f"{name} ({max(dates)}, status: {row.get('status', '?')})")
    if past:
        r.add("timeline", "attention", f"{len(past)} milestone(s) past their latest date but not closed: "
              + "; ".join(past[:8]) + (" …" if len(past) > 8 else ""))
    if unreadable:
        r.add("timeline", "info", f"{len(unreadable)} open milestone(s) with no full YYYY-MM-DD date: "
              + "; ".join(unreadable[:8]) + (" …" if len(unreadable) > 8 else ""))

    authority = last_updated(text)
    if authority is None:
        return
    for other in sorted((root / "4-Trackers").glob("*.md")):
        if other.name in ("tasks.md", "timeline.md") or other.name.startswith("_"):
            continue
        if not re.search(r"gantt|timeline|schedule|calendar", other.name, re.IGNORECASE):
            continue
        derived = last_updated(read(other))
        if derived is None:
            derived = datetime.fromtimestamp(other.stat().st_mtime).date()
        if derived < authority:
            r.add("timeline", "attention", f"`4-Trackers/{other.name}` (updated {derived}) is older than "
                  f"`timeline.md` (updated {authority}). `timeline.md` is the authority; treat the other "
                  "file as stale until it is regenerated or marked superseded.")


def check_views(root: Path, today: date, r: Report):
    """Plan E3. Generated views must match the revision of the tracker they claim; dependency problems
    come from timeline_views.py itself so the two scripts can never disagree about them."""
    text = read(root / "4-Trackers" / "timeline.md")
    live = {"timeline": REVISION.search(text) if text and not is_stub(text) else None}
    meetings = read(root / "4-Trackers" / "meetings.md")
    live["meetings"] = REVISION.search(meetings) if meetings and not is_stub(meetings) else None
    scripts = {"timeline": "timeline_views.py", "meetings": "meetings_view.py"}
    views = sorted((root / "4-Trackers" / "_views").glob("*.md"))
    for view in views:
        stamp = re.search(r"generated from (timeline|meetings)\.md revision (\w+)", read(view))
        source = stamp.group(1) if stamp else ("meetings" if view.name.startswith("meetings") else "timeline")
        rev = live[source]
        if rev and (stamp is None or stamp.group(2) != rev.group(1)):
            r.add("timeline" if source == "timeline" else "meetings", "attention",
                  f"`4-Trackers/_views/{view.name}` was generated from {source} "
                  f"revision {stamp.group(2) if stamp else 'unknown'}; the {source} is at revision "
                  f"{rev.group(1)}. Run `python .familiar/scripts/{scripts[source]}`.")
    if not live["timeline"]:
        return
    try:
        # Importing would otherwise leave a __pycache__ folder in the project: this script writes nothing.
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import timeline_views
    except ImportError:
        return
    rows = timeline_views.parse(text)
    for problem in timeline_views.dependency_problems(rows):
        level = "info" if "cannot be checked" in problem else "attention"
        r.add("timeline", level, problem)
    if rows and not any(row["has_id"] for row in rows):
        r.add("timeline", "info", "The timeline uses the older layout (no milestone IDs). Convert rows "
              "one at a time at check-in (`state-schema.md` → Tracker conventions).")


def check_meetings(root: Path, today: date, r: Report):
    """meetings.md is optional. When present, whatever meetings_view.py cannot read is reported, and a
    meeting that names a milestone must name one that exists."""
    text = read(root / "4-Trackers" / "meetings.md")
    if not text or is_stub(text):
        return
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import meetings_view
    except ImportError:
        return
    result = meetings_view.expand(text, today, 0)
    for problem in result["problems"]:
        r.add("meetings", "attention", problem)
    timeline = read(root / "4-Trackers" / "timeline.md")
    known = {row["id"].lower() for row in timeline_rows(timeline)}
    series_rows, other_rows = meetings_view.sections(text)
    for row in series_rows + other_rows:
        ref = row.get("milestone", "").strip()
        if ref and ref not in meetings_view.BLANK and known and ref.lower() not in known:
            r.add("meetings", "attention", f"{row.get('id', '?')} names milestone {ref}, which is not in the timeline.")


def timeline_rows(text: str) -> list[dict]:
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import timeline_views
    except ImportError:
        return []
    return timeline_views.parse(text) if text else []


def check_knowledge(root: Path, r: Report):
    folder = root / "3-Knowledge"
    index = read(folder / "_knowledge-index.md")
    if not folder.is_dir():
        return
    pages = [p for p in folder.glob("*.md") if not p.name.startswith("_")]
    missing = [p.name for p in pages if p.stem not in index]
    if missing:
        r.add("knowledge", "attention", f"{len(missing)} knowledge page(s) not in `_knowledge-index.md`: "
              + ", ".join(missing[:10]))
    for target in re.findall(r"\]\(\./?([^)#]+\.md)\)", index):
        if not (folder / target).is_file():
            r.add("knowledge", "attention", f"`_knowledge-index.md` links to `{target}`, which does not exist.")


def check_inputs(root: Path, r: Report):
    folder = root / "1-Inputs"
    if not folder.is_dir():
        return
    by_hash = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("~$") or path.name == "_about.md" \
                or path.stat().st_size > HASH_LIMIT:
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError:
            continue
        by_hash.setdefault(digest.hexdigest(), []).append(path.relative_to(root).as_posix())
    for paths in by_hash.values():
        if len(paths) > 1:
            r.add("inputs", "info", "Byte-identical input files (same content, different names): "
                  + " = ".join(f"`{p}`" for p in paths) + ". Keep both; make sure they were ingested once.")


def check_pages(root: Path, r: Report):
    """Index rows vs page front matter (index_check.py) and citation stamps vs the page (ref_count.py),
    through those scripts' own code so the checks can never disagree."""
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_check
        import ref_count
    except ImportError:
        return
    if (root / "3-Knowledge" / "_knowledge-index.md").is_file():
        result = index_check.check(root)
        for item in result["mismatches"]:
            r.add("knowledge", "attention", f"`_knowledge-index.md` row for {item['page']} disagrees with the page: "
                  + "; ".join(item["problems"]))
    unstamped, off = [], []
    for page in sorted((root / "3-Knowledge").glob("*.md")) if (root / "3-Knowledge").is_dir() else []:
        if page.name.startswith("_"):
            continue
        counted = ref_count.count(read(page))
        if counted["stamp"] in (None, "freehand"):
            unstamped.append(page.name)
        elif counted["matches_stamp"] is False:
            off.append(f"{page.name} (stamp {counted['stamp_tagged']}/{counted['stamp_carried']}, "
                       f"page {counted['claims_tagged']}/{counted['carried']})")
    if off:
        r.add("knowledge", "attention", f"{len(off)} page(s) whose `ref_carry_through` stamp is more than 10% "
              "away from the tags on the page: " + "; ".join(off[:6]))
    if unstamped:
        r.add("knowledge", "info", f"{len(unstamped)} page(s) with no standard `ref_carry_through` stamp "
              "(written before it was required, or freehand): " + ", ".join(unstamped[:8])
              + (" …" if len(unstamped) > 8 else ""))


def check_review_dates(root: Path, today: date, r: Report):
    """Knowledge pages past their `review_by` date (ingest.md → Source dates and conflicts). A page
    already superseded, or with no ISO date ("not scheduled"), is skipped. Reports only; it never
    re-dates or rewrites a page."""
    folder = root / "3-Knowledge"
    if not folder.is_dir():
        return
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_check
    except ImportError:
        return
    overdue = []
    for page in sorted(folder.glob("*.md")):
        if page.name.startswith("_"):
            continue
        fm = index_check.front_matter(read(page))
        if index_check.page_names(fm.get("superseded_by", "")):
            continue
        dates = iso_dates(fm.get("review_by", ""))
        if dates and dates[0] < today:
            overdue.append(f"{page.name} (review by {dates[0]})")
    if overdue:
        level, prefix = ("attention", "") if r.lifecycle == "active" else ("info", f"(project is {r.lifecycle}) ")
        r.add("knowledge", level, f"{prefix}{len(overdue)} knowledge page(s) past their review-by date; check "
              "each against its source before relying on it: " + "; ".join(overdue[:8])
              + (" …" if len(overdue) > 8 else ""))


def register_rows(text: str) -> list[dict]:
    """Rows of `3-Knowledge/_source-register.md` as dicts keyed by its header."""
    header, rows = None, []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if header is None:
            header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def check_extracts(root: Path, r: Report):
    """Each page reaches its full-text cache as `_extract-<source_id>.md` (lookup Routing step 3), with
    the register's `Source ID` for the page standing in when the page has no `source_id`. Reports a page
    from a PDF whose ID names no extract, and an extract no page or register row names. Both are info:
    lookup falls back to an extract named after the page or source file, so nothing is unreachable."""
    folder = root / "3-Knowledge"
    if not folder.is_dir():
        return
    try:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_check
    except ImportError:
        return
    extracts = {p.stem[len("_extract-"):] for p in folder.glob("_extract-*.md")}
    rows = register_rows(read(folder / "_source-register.md"))
    by_page, pdf_ids = {}, set()
    for row in rows:
        if row.get("Path", "").lower().endswith(".pdf"):
            pdf_ids.add(row.get("Source ID", ""))
        for page in (p.strip() for p in row.get("Knowledge page", "").split(";")):
            if page:
                by_page.setdefault(Path(page).name, row.get("Source ID", ""))
    named, missing = {row.get("Source ID", "") for row in rows}, []
    for page in sorted(folder.glob("*.md")):
        if page.name.startswith("_"):
            continue
        fm = index_check.front_matter(read(page))
        sid = fm.get("source_id", "").strip("\"'` ") or by_page.get(page.name, "")
        if not sid:
            continue
        named.add(sid)
        if sid not in extracts and (sid in pdf_ids or fm.get("source", "").lower().endswith(".pdf")):
            missing.append(f"{page.name} ({sid})")
    orphans = sorted(extracts - named)
    if missing:
        r.add("knowledge", "info", f"{len(missing)} page(s) from a PDF with no `_extract-<source_id>.md` "
              "under that ID, so lookup has to guess the full text's file: " + "; ".join(missing[:8])
              + (" …" if len(missing) > 8 else ""))
    if orphans:
        r.add("knowledge", "info", f"{len(orphans)} extract(s) no page `source_id` or register row names, so "
              "lookup reaches them only by guessing: " + ", ".join(f"`_extract-{o}.md`" for o in orphans[:8])
              + (" …" if len(orphans) > 8 else ""))


def check_register(root: Path, r: Report):
    """Plan E2. The register is written by the ingest skill's source_register.py; this only reads it."""
    text = read(root / "3-Knowledge" / "_source-register.md")
    inputs = [p for p in (root / "1-Inputs").rglob("*") if p.is_file() and not p.name.startswith("~$")
              and p.name not in {".DS_Store", "Thumbs.db", "desktop.ini", "_about.md"}] \
        if (root / "1-Inputs").is_dir() else []
    if not text:
        if inputs:
            r.add("inputs", "info", f"No `3-Knowledge/_source-register.md` yet, so nothing records what "
                  f"happened to the {len(inputs)} input file(s). The next ingest starts it.")
        return
    recorded, open_runs, dead_pages = set(), [], []
    for row in register_rows(text):
        recorded.add(row.get("Path", ""))
        recorded.update(a.strip() for a in row.get("Aliases", "").split(";") if a.strip())
        if row.get("Status") in ("partial", "failed"):
            open_runs.append(f"{row.get('Source ID')} ({row.get('Status')})")
        # One source can feed several pages: "3-Knowledge/a.md; 3-Knowledge/b.md".
        for page in (p.strip() for p in row.get("Knowledge page", "").split(";")):
            if page and row.get("Status") != "superseded" and not (root / page).is_file():
                dead_pages.append(f"{row.get('Source ID')} -> `{page}`")
    unrecorded = [p.relative_to(root).as_posix() for p in inputs
                  if p.relative_to(root).as_posix() not in recorded]
    if open_runs:
        r.add("inputs", "attention", f"{len(open_runs)} ingest(s) recorded as partial or failed: "
              + "; ".join(open_runs[:8]) + ". Resume them rather than starting again.")
    if dead_pages:
        r.add("inputs", "attention", "Source register rows point at knowledge pages that do not exist: "
              + "; ".join(dead_pages[:8]))
    if unrecorded:
        r.add("inputs", "info", f"{len(unrecorded)} input file(s) not in the source register: "
              + ", ".join(f"`{p}`" for p in unrecorded[:8]) + (" …" if len(unrecorded) > 8 else ""))


def check_hygiene(root: Path, r: Report):
    for sub in ("4-Trackers", "5-System"):
        for path in (root / sub).rglob("*.md"):
            if CONFLICT_COPY.search(path.name):
                r.add("hygiene", "attention", f"`{path.relative_to(root).as_posix()}` looks like a sync "
                      "conflict copy. Do not read it as state; compare it with the original and remove it by hand.")
    stale_prefix = []
    for rel in ("5-System/Memory/status-snapshot.md", "4-Trackers/tasks.md", "4-Trackers/timeline.md",
                "5-System/project-config.md", "5-System/user-preferences.md"):
        if "/familiar:" in read(root / rel):
            stale_prefix.append(rel)
    if stale_prefix:
        r.add("hygiene", "info", "Retired `/familiar:<skill>` command names in " + ", ".join(
              f"`{p}`" for p in stale_prefix) + ". Skills are invoked as `/<skill>`.")
    prefs = read(root / "5-System" / "user-preferences.md")
    if prefs and not re.search(r"familiar signature\**:?\**\s*\S", prefs, re.IGNORECASE):
        r.add("hygiene", "info", "No familiar signature of this project's own; the bind line and the fleet hub "
              "show the default from `~/.familiar/user-preferences.md`. Set one with `/system-settings` → Preferences.")


def listed(names: list[str], limit: int = 6) -> str:
    shown = ", ".join(f"`{n if len(n) <= 70 else n[:67] + '...'}`" for n in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def check_drafts(root: Path, today: date, r: Report):
    """Read-only Drafts hygiene (plan DRAFTS-HARDEN G4). Never opens `.scratch/` contents beyond
    sizes and ages, and does not descend into `node_modules/` or `__pycache__/`."""
    drafts = root / "2-Drafts"
    if drafts.is_dir():
        index_text = read(drafts / "_draft-index.md")
        stems = set()
        rows = []
        for line in index_text.splitlines():
            if not line.startswith("|") or set(line) <= set("|-: "):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0] not in ("Deliverable", "_(none yet)_"):
                rows.append(cells)
                # A row's file is a backticked path or a markdown link; either way, its stem.
                target = re.search(r"\]\(([^)]+)\)", cells[1]) if len(cells) > 1 else None
                shown = (target[1].replace("%20", " ") if target else cells[1].strip("`")) if len(cells) > 1 else ""
                if shown:
                    stems.add(Path(shown).stem)
        # Every version a deliverable's own `_versions.md` lists is indexed through that log: at v2
        # the index row names v2 while v1 sits beside it in the folder, which is the normal path.
        logged_slugs = set()
        for log in drafts.glob("*/_versions.md"):
            logged_slugs.add(log.parent.name)
            for line in read(log).splitlines():
                cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
                if len(cells) > 3 and DRAFT_NAME.match(cells[3]):
                    stems.add(Path(cells[3]).stem)
        if len(index_text.encode("utf-8")) > INDEX_BYTES_LIMIT:
            r.add("drafts", "info", f"`_draft-index.md` is {len(index_text.encode('utf-8')) // 1024} KB (limit "
                  f"{INDEX_BYTES_LIMIT // 1024} KB). Per-version rationale belongs in each deliverable's `_versions.md`.")
        long_rows = [c[0] for c in rows if any(len(v) > (INDEX_PATH_LIMIT if i == 1 else INDEX_CELL_LIMIT)
                                               for i, v in enumerate(c))]
        if long_rows:
            r.add("drafts", "info", "Index rows with an over-long cell: " + listed(long_rows)
                  + ". Keep every cell short; move the detail to `_versions.md`.")

        unindexed, off_name, junk, flat = [], [], [], {}
        for here, dirs, files in os.walk(drafts):
            here_path = Path(here)
            for d in list(dirs):
                rel = (here_path / d).relative_to(root).as_posix()
                if d in DRAFT_SKIP_DIRS:
                    dirs.remove(d)
                elif d in DRAFT_JUNK_DIRS:
                    dirs.remove(d)
                    junk.append(rel + "/")
                elif (here_path / d / "[Content_Types].xml").is_file() or (here_path / d / "word").is_dir():
                    dirs.remove(d)
                    junk.append(rel + "/ (unzipped package)")
            for name in files:
                path = here_path / name
                rel = path.relative_to(root).as_posix()
                if name in DRAFT_SKIP_FILES or name.startswith("~$") or name.endswith(".tmp"):
                    continue
                if path.suffix.lower() in DRAFT_JUNK_EXT:
                    junk.append(rel)
                    continue
                m = DRAFT_NAME.match(name)
                if here_path == drafts and m:
                    flat.setdefault(m["slug"].lower(), set()).add(int(m["n"]))
                if any(name.startswith(stem) for stem in stems if stem):
                    continue
                if m and here_path.name == m["slug"].lower() and m["slug"].lower() in logged_slugs:
                    continue
                (unindexed if m else off_name).append(path.relative_to(drafts).as_posix())
        if unindexed:
            r.add("drafts", "attention", f"{len(unindexed)} draft file(s) not in `_draft-index.md`: "
                  + listed(unindexed) + ". Possible external edit; register with `draft_save.py adopt` or index them.")
        if off_name:
            r.add("drafts", "info", f"{len(off_name)} file(s) in `2-Drafts/` off the naming convention "
                  "(`YYYY-MM-DD_<slug>_<STATUS>_vN`): " + listed(off_name) + ".")
        if junk:
            r.add("drafts", "attention", "Scripts, caches or unzipped packages in `2-Drafts/`: " + listed(junk)
                  + ". Scratch belongs in `.scratch/`; Workspace tidy can move these.")
        legacy = sorted(s for s, v in flat.items() if len(v) >= 2)
        if legacy:
            r.add("drafts", "attention", "Deliverables with 2+ versions still flat in `2-Drafts/` (legacy layout): "
                  + listed(legacy) + ". Run `python .familiar/scripts/draft_save.py migrate --dry-run`, show the "
                  "user the plan, and run it without `--dry-run` once they agree. Every new version goes through "
                  "`draft_save.py next`, including from a build script.")
        elif RETIRED_FLAT_RULE.search(index_text):
            r.add("drafts", "info", "`_draft-index.md` still states the retired flat-layout rule. "
                  "`python .familiar/scripts/draft_save.py migrate` replaces it.")

    strays = []
    try:
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and not TOP_LEVEL_OK.match(entry.name):
                strays.append(entry.name + "/")
    except OSError:
        pass
    if strays:
        tmp = [s for s in strays if TOP_LEVEL_TMP.search(s)]
        r.add("drafts", "attention" if tmp else "info", "Top-level folder(s) outside the workspace map: "
              + listed(strays) + ". New outputs go in `2-Drafts/`; scratch goes in `.scratch/`.")

    scratch = root / ".scratch"
    if scratch.is_dir():
        size, newest, count = 0, 0.0, 0
        for here, dirs, files in os.walk(scratch):
            for name in files:
                try:
                    st = (Path(here) / name).stat()
                except OSError:
                    continue
                size, count, newest = size + st.st_size, count + 1, max(newest, st.st_mtime)
        age = (today - datetime.fromtimestamp(newest).date()).days if count else 0
        if size > SCRATCH_BYTES_LIMIT or (count and age > SCRATCH_AGE_DAYS):
            r.add("drafts", "info", f"`.scratch/` holds {count} file(s), {size // (1024 * 1024)} MB, newest {age} "
                  "days old. Workspace tidy can move it to `_del/`.")


def check_handoffs(root: Path, today: date, r: Report):
    """Open or resumed handoffs older than a week are usually finished or replaced threads that keep
    appearing in the open list. Report them; closing one is the user's call through `/handoff`."""
    stale = []
    for path in sorted((root / "5-System" / "Handoffs").glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = read(path)
        status = HANDOFF_STATUS.search(text)
        if not status:
            continue
        dates = iso_dates(status.group(2)) or iso_dates((HANDOFF_WRITTEN.search(text) or [None, ""])[1] or "")
        dates = dates or iso_dates(path.name[:10])
        if dates and (today - max(dates)).days > STALE_HANDOFF_DAYS:
            covers = HANDOFF_COVERS.search(text)
            label = (covers.group(1).strip() if covers else path.stem)[:70]
            stale.append(f"{path.name} ({status.group(1).lower()} since {max(dates)}: {label})")
    if stale:
        r.add("handoffs", "info", f"{len(stale)} handoff(s) open or resumed for more than {STALE_HANDOFF_DAYS} "
              "days: " + "; ".join(stale[:8]) + (" …" if len(stale) > 8 else "")
              + ". If a thread is finished or replaced, close it with `/handoff`.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--today", help="YYYY-MM-DD, for testing; defaults to the system date")
    ap.add_argument("--root", help="project folder; defaults to the nearest parent with .familiar-project")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_root(start.resolve())
    if root is None:
        print("ERROR: no .familiar-project found at or above " + str(start), file=sys.stderr)
        sys.exit(2)
    today = date.fromisoformat(args.today) if args.today else date.today()

    report = Report(snapshot_lifecycle(root))
    for check in (check_setup, check_knowledge, check_pages, check_extracts, check_inputs, check_register,
                  check_hygiene):
        check(root, report)
    for check in (check_snapshot, check_tasks, check_timeline, check_views, check_meetings, check_drafts,
                  check_handoffs, check_review_dates):
        check(root, today, report)

    if args.json:
        print(json.dumps({"root": str(root), "today": today.isoformat(), "findings": report.findings}, indent=2))
        return
    if not report.findings:
        print(f"project check ({today}): no findings")
        return
    order = {"attention": 0, "info": 1}
    print(f"project check ({today}): {len(report.findings)} finding(s)")
    for f in sorted(report.findings, key=lambda f: order[f["level"]]):
        print(f"- [{f['level']}] {f['area']}: {f['message']}")


if __name__ == "__main__":
    main()
