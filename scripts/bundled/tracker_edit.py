#!/usr/bin/env python3
"""tracker_edit.py — one validated change to `4-Trackers/timeline.md` or `tasks.md`.

    python .familiar/scripts/tracker_edit.py milestone --id M-04 --due 2026-11-03 --reason "client moved it"
    python .familiar/scripts/tracker_edit.py milestone --id M-07 --status not-applicable --reason "dropped from SOW"
    python .familiar/scripts/tracker_edit.py task --id T-12 --done
    python .familiar/scripts/tracker_edit.py task --id T-12 --due 2026-11-03 --reason "waiting on data"
    python .familiar/scripts/tracker_edit.py task --id T-12 --reopen
    python .familiar/scripts/tracker_edit.py task --id T-12 --title "Draft the CDA response" --reason "scope grew"
    python .familiar/scripts/tracker_edit.py milestone --id M-04 --title "Final report" --reason "renamed in SOW"
    python .familiar/scripts/tracker_edit.py meeting add-series --name "Check-in" --kind internal --rule weekly:tue --time 11:30-12:00 --start 2026-10-13 --end 2027-03-16
    python .familiar/scripts/tracker_edit.py meeting add-oneoff --name "Evidence presentation" --kind client --date 2026-11-04 --time 14:00-15:00 --milestone M-06
    python .familiar/scripts/tracker_edit.py meeting move --id S-01 --from 2026-10-20 --to 2026-10-21 --reason "out of office"
    python .familiar/scripts/tracker_edit.py meeting cancel --id S-01 --date 2026-10-27 --reason "holiday"
    python .familiar/scripts/tracker_edit.py meeting edit-series --id S-01 --end 2027-02-02 --reason "wraps early"
    python .familiar/scripts/tracker_edit.py meeting rename --id S-01 --name "Client weekly" --reason "clearer"
    python .familiar/scripts/tracker_edit.py meeting agenda --id S-01 --date 2026-10-27 --items "Actions; Timeline check"
    python .familiar/scripts/tracker_edit.py meeting agenda --id S-01 --date standing --items "Actions"
    python .familiar/scripts/tracker_edit.py meeting agenda --id X-01 --date 2026-11-04 --none "walkthrough only"
    ... --dry-run            # show the exact before/after lines, write nothing
    ... --expect-revision 4  # refuse unless the file is still at the revision the preview saw
    ... --json               # machine-readable result (the fleet hub reads this)

What "a correct edit" means here is `state-schema.md` → Tracker conventions and `calendar-rules.md`:
the revision is checked before and incremented after (`CORE-STALE-WRITE`), the replacement is atomic,
a date move or status change is appended to `5-System/Memory/status-log.md` (old → new, reason), and
`4-Trackers/_views/` is regenerated. Nothing is ever deleted (`CORE-NO-DELETE`): "no longer needed"
is the milestone status `not applicable`.

It changes the one row it was asked to change and nothing else. It does NOT move downstream
milestones, rewrite `status-snapshot.md`, or touch other tasks; it lists every other place that still
names the old date or ID (`unpropagated`) and every open milestone or task that depends on the
changed one (`dependents`), so the caller can finish the propagation `calendar-rules.md` requires.
A `done` milestone or task with open dependents is reported, not refused.

A tracker written before the revision stamp existed gets the schema's three header lines on its first
edit (`stamp_added` in the result), so the revision check works from then on. A task's date can replace
`Due:` / `Follow up:` (a date or `not set`) or `No date: <reason>`; a done task moves under `## Done`
and a reopened one back under `## Open` when those sections exist.

`meeting` edits `4-Trackers/meetings.md` (rules in `state-schema.md` → Tracker conventions, parsed by the
sibling `meetings_view.py`): add a series or a one-off (starting the file when a project has none), move or
cancel one date of a series or a single meeting, or edit a series in place (end it, pause it, change its
time, rule or attendees; the old values are in the status log), rename a series or single meeting, or set
one date's agenda (or a series' standing agenda) under `## Agendas`. A moved date takes its agenda with it.
It refuses a change that would leave the file with a problem it did not have, reports a new overlap as
`conflicts`, and ends with a reminder to update your own calendar. Rows are never deleted.

`--title` renames a milestone or a task (a wrapped task title becomes one line). IDs do not change, so
references by ID still hold; places that name the old title in words are listed as `unpropagated`.

Standard library only. Exit 0 on success (or a clean dry run), 1 on a refusal, with the reason on
stderr (or in the JSON with --json).
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True  # never leave __pycache__ in a project

REVISION = re.compile(r"<!--\s*revision:\s*(\d+)\s*-->")
LAST_UPDATED = re.compile(r"(<!--\s*last-updated:\s*)([^>]*?)(\s*-->)")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATUSES = ("not started", "in progress", "on track", "at risk", "slipped", "done", "not applicable")
TASK_LINE = re.compile(r"^(\s*[-*]\s+\[)( |x|X)(\]\s+)(.*)$")
FIELD = re.compile(r"(?P<name>Due|Follow up):\s*(?P<value>\d{4}-\d{2}-\d{2}|not set)"
                   r"|No date:\s*.*?(?=\s+—\s|\.\s+(?:Owner|Blocked by|Due|Follow up|Track)\b|\.?[ \t]*\r?$)", re.M)
DONE_MARK = re.compile(r"(?:[ \t]+—[ \t]+Done:[ \t]*\d{4}-\d{2}-\d{2})+[ \t]*(?=\r?$)", re.M)


class Refusal(Exception):
    pass


def find_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".familiar-project").is_file():
            return folder
    return None


def valid_date(text: str) -> bool:
    if not ISO.match(text):
        return False
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def valid_due(text: str) -> bool:
    """A full date, a window `YYYY-MM-DD to YYYY-MM-DD`, or `No date: <reason>` — nothing else."""
    if text.lower().startswith("no date:"):
        return bool(text[8:].strip())
    parts = [p.strip() for p in text.split(" to ")]
    return len(parts) in (1, 2) and all(valid_date(p) for p in parts)


def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def revision_of(text: str) -> int:
    m = REVISION.search(text)
    return int(m.group(1)) if m else 0


def bump(text: str, today: str) -> str:
    """Increment the revision and refresh last-updated, leaving everything else byte-identical."""
    text = REVISION.sub(lambda m: f"<!-- revision: {int(m.group(1)) + 1} -->", text, count=1)
    return LAST_UPDATED.sub(lambda m: f"{m.group(1)}{today}{m.group(3)}", text, count=1)


def ensure_stamp(text: str, today: str) -> tuple[str, bool]:
    """Add the schema's header lines a tracker is missing (`state-schema.md` → Required metadata), with
    revision 0, so `bump` makes this edit revision 1. Returns (text, whether anything was added)."""
    if REVISION.search(text):
        return text, False
    nl = "\r\n" if "\r\n" in text else "\n"
    head = [] if "familiar-state-schema:" in text else ["<!-- familiar-state-schema: 1.0 -->"]
    head.append("<!-- revision: 0 -->")
    if not LAST_UPDATED.search(text):
        head.append(f"<!-- last-updated: {today} -->")
    return nl.join(head) + nl + text, True


def section_of(lines: list[str], index: int) -> str:
    """The `## ` heading a line sits under, lower-cased, or '' above the first one."""
    for line in reversed(lines[:index]):
        if line.startswith("## "):
            return line[3:].strip().lower()
    return ""


def move_block(lines: list[str], start: int, end: int, block: list[str], heading: str, newline: str):
    """Put `block` (replacing lines[start:end]) under `## <heading>`: Done at the top, Open at the end.
    Returns the new lines, or None when the file has no such section."""
    rest = lines[:start] + lines[end:]
    at = next((i for i, l in enumerate(rest) if re.match(r"^##\s+" + heading + r"\b", l, re.IGNORECASE)), None)
    if at is None:
        return None
    if not block[-1].endswith(("\n", "\r")):
        block[-1] += newline
    if heading == "Done":
        insert = at + 1
        while insert < len(rest) and not rest[insert].strip():
            insert += 1
        if insert < len(rest) and rest[insert].strip().lower().startswith("(none yet"):
            del rest[insert]  # the scaffold's placeholder, replaced by the first real entry
    else:
        insert = next((i for i in range(at + 1, len(rest)) if rest[i].startswith("## ")), len(rest))
        while insert > at + 1 and not rest[insert - 1].strip():
            insert -= 1
    rest[insert:insert] = block
    return rest


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def split_row(line: str) -> list[str]:
    parts = line.rstrip("\r\n").split("|")
    return parts[1:-1]


def milestone_edit(text: str, mid: str, due, status, confirmed, title=None):
    """Return (new_text, before_line, after_line, old_cells, new_cells, row_track)."""
    lines = text.splitlines(keepends=True)
    header, hits = None, []
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            if header is not None and hits:
                break
            continue
        cells = [c.strip().lower() for c in split_row(line)]
        if header is None:
            if "id" in cells and "due" in cells and "status" in cells:
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if cells[header.index("id")] == mid.lower():
            hits.append(index)
    if header is None:
        raise Refusal("timeline.md has no table with ID, Due and Status columns (the older layout has no IDs to edit by)")
    if not hits:
        raise Refusal(f"{mid} is not a milestone in timeline.md")
    if len(hits) > 1:
        raise Refusal(f"{mid} appears {len(hits)} times in timeline.md; fix the duplicate by hand")
    index = hits[0]
    raw = lines[index]
    newline = raw[len(raw.rstrip("\r\n")):]
    parts = raw.rstrip("\r\n").split("|")
    if len(parts) - 2 != len(header):
        raise Refusal(f"{mid}'s row has {len(parts) - 2} cells but the table has {len(header)} columns")
    old = {name: parts[1 + i].strip() for i, name in enumerate(header)}
    new = dict(old)
    for column, value in (("due", due), ("status", status), ("confirmed", confirmed), ("milestone", title)):
        if value is None:
            continue
        if column == "confirmed" and "confirmed" not in header:
            raise Refusal("this timeline has no Confirmed column")
        if column == "milestone" and "milestone" not in header:
            raise Refusal("this timeline has no Milestone column")
        parts[1 + header.index(column)] = f" {value} "
        new[column] = value
    after = "|".join(parts) + newline
    lines[index] = after
    return "".join(lines), raw.rstrip("\r\n"), after.rstrip("\r\n"), old, new, old.get("track", "")


def task_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end-exclusive) line spans of every checkbox task with its indented continuation lines."""
    spans, start = [], None
    for index, line in enumerate(lines + [""]):
        is_task = bool(TASK_LINE.match(line))
        continues = start is not None and line.startswith((" ", "\t")) and line.strip() and not is_task
        if start is not None and not continues:
            spans.append((start, index))
            start = None
        if is_task:
            start = index
    return spans


def task_id_pattern(tid: str) -> re.Pattern:
    return re.compile(r"(?:\*\*" + re.escape(tid) + r"\*\*|\*\*" + re.escape(tid) + r"\s+—)")


def clean_title(value: str) -> str:
    value = " ".join((value or "").split())
    if not value:
        raise Refusal("--title cannot be empty")
    if "|" in value or "**" in value or "—" in value:
        raise Refusal("--title cannot contain '|', '**' or '—' (they separate a task's fields)")
    return value


def retitle(block: str, tid: str, title: str) -> tuple[str, str]:
    """(new block, old title): the title in a task's lines replaced, either the bold lead `**T-12 — Title.**`
    or the words after `**T-12**` up to the next ` — ` or field. A wrapped title becomes one line."""
    lead = re.compile(r"\*\*" + re.escape(tid) + r"\s+—\s+(.+?)\*\*", re.S)
    m = lead.search(block)
    if not m:
        ident = re.compile(r"\*\*" + re.escape(tid) + r"\*\*[ \t]*(?:—[ \t]*)?")
        m = ident.search(block)
        if not m:
            raise Refusal(f"{tid}'s title is not in a form this script can find; rename it by hand")
        m = re.compile(r"(.*?)(?=[ \t]+—[ \t]|\s*blocked by:|\r?\n[ \t]*(?:owner|due|follow up|no date|blocked by)\b"
                       r"|\r?\n[ \t]*[-*][ \t]|\s*\Z)", re.S | re.I).match(block, m.end())
    old = " ".join(m.group(1).split())
    if not old:
        raise Refusal(f"{tid} has no title to replace; rename it by hand")
    if old.endswith(".") and not title.endswith((".", "?", "!")):
        title += "."
    return block[:m.start(1)] + title + block[m.end(1):], old


def task_edit(text: str, tid: str, today: str, done, reopen, due, no_date, title=None):
    lines = text.splitlines(keepends=True)
    spans = task_blocks(lines)
    pattern = task_id_pattern(tid)
    hits = [(s, e) for s, e in spans if pattern.search("".join(lines[s:e]).split("\n", 1)[0])]
    if not hits:
        raise Refusal(f"{tid} is not a task in tasks.md")
    if len(hits) > 1:
        raise Refusal(f"{tid} appears {len(hits)} times in tasks.md; fix the duplicate by hand")
    start, end = hits[0]
    block = lines[start:end]
    before = "".join(block).rstrip("\r\n")
    first = block[0]
    newline = first[len(first.rstrip("\r\n")):] or "\n"
    m = TASK_LINE.match(first.rstrip("\r\n"))
    checked = m.group(2) in ("x", "X")
    if done:
        if checked:
            raise Refusal(f"{tid} is already done")
        block[0] = f"{m.group(1)}X{m.group(3)}{m.group(4)}{newline}"
        block = DONE_MARK.sub("", "".join(block)).splitlines(keepends=True)
        # The mark goes at the end of the whole entry, so a wrapped title is not split.
        body = block[-1].rstrip("\r\n")
        block[-1] = f"{body} — Done: {today}{block[-1][len(body):]}"
    elif reopen:
        if not checked:
            raise Refusal(f"{tid} is already open")
        block[0] = f"{m.group(1)} {m.group(3)}{m.group(4)}{newline}"
        block = DONE_MARK.sub("", "".join(block)).splitlines(keepends=True)
    elif title is not None:
        joined, old_title = retitle("".join(block), tid, title)
        if old_title.rstrip(".") == title.rstrip("."):
            raise Refusal(f"{tid} is already called that; nothing to change")
        # The task's own parser must read the new title back, or the edit is refused.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import task_frontier
        finally:
            sys.path.pop(0)
        read_back = [t["title"] for t in task_frontier.parse(joined) if (t["id"] or "").lower() == tid.lower()]
        if [t.rstrip(".") for t in read_back] != [title.rstrip(".")]:
            raise Refusal(f"{tid}'s new title would not read back cleanly ({read_back}); rename it by hand")
        block = joined.splitlines(keepends=True)
        before_title = old_title
    else:
        if checked:
            raise Refusal(f"{tid} is done; reopen it before changing its date")
        joined = "".join(block)
        fields = list(FIELD.finditer(joined))
        if len(fields) != 1:
            raise Refusal(f"{tid} has {len(fields)} Due / Follow up / No date fields; change it by hand")
        replacement = f"No date: {no_date}" if no_date else f"{fields[0].group('name') or 'Due'}: {due}"
        joined = joined[:fields[0].start()] + replacement + joined[fields[0].end():]
        block = joined.splitlines(keepends=True)
    after = "".join(block).rstrip("\r\n")
    moved = False
    # Keep Open open: a done task goes under `## Done`, a reopened one back under `## Open`.
    target = "Done" if done else "Open" if reopen and section_of(lines, start).startswith("done") else None
    if target:
        rest = move_block(lines, start, end, block, target, newline)
        if rest is not None:
            lines, moved = rest, True
    if not moved:
        lines[start:end] = block
    return "".join(lines), before, after, checked, moved, (before_title if title is not None else None)


def references(text: str, needles: list[str], skip_line: str) -> list[str]:
    """Lines (trimmed) that still name any needle, other than the edited line itself."""
    found = []
    for line in text.splitlines():
        if line.rstrip() == skip_line.rstrip():
            continue
        if any(n and n in line for n in needles):
            found.append(line.strip()[:160])
    return found


def regenerate_views(root: Path, name: str = "timeline_views.py") -> str:
    script = Path(__file__).resolve().parent / name
    if not script.is_file():
        return f"skipped: {name} not found beside tracker_edit.py"
    done = subprocess.run([sys.executable, "-B", str(script), "--root", str(root)], capture_output=True, text=True)
    return "regenerated" if done.returncode == 0 else "FAILED: " + (done.stderr or done.stdout).strip()[:200]


def log_entry(root: Path, today: str, kind: str, headline: str, body: str, track: str) -> tuple[Path, str]:
    path = root / "5-System" / "Memory" / "status-log.md"
    head = f"## [{today}] {track + ' | ' if track else ''}{kind} | {headline}"
    return path, f"\n{head}\n{body}\n"


def append_log(path: Path, entry: str) -> None:
    existing = read_text(path) if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        entry = "\n" + entry
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(entry)


# ---- meetings.md -----------------------------------------------------------------------------

MEETINGS_STUB = """<!-- familiar-state-schema: 1.0 -->
<!-- revision: 0 -->
<!-- last-updated: not-yet-written -->
# Meetings — NOT YET WRITTEN

Planned internal and client meetings. Edited here only; `4-Trackers/_views/meetings-next-weeks.md` is
generated from this file by `.familiar/scripts/meetings_view.py`. Column values and rules:
`.familiar/core/state-schema.md` → Tracker conventions. Notes only: update your own calendar to match.
Agendas go under `## Agendas` as `### <ID> <YYYY-MM-DD>` (or `### <S-ID> standing`) with `- topic` bullets.

## Series

(none yet)

| ID | Meeting | Kind | Rule | Time | Start | End | Attendees | Milestone | Status |
|---|---|---|---|---|---|---|---|---|---|

## One-offs and exceptions

(none yet)

| ID | Meeting | Kind | Date | Time | Attendees | Milestone | Series | Replaces | Status | Note |
|---|---|---|---|---|---|---|---|---|---|---|

## Agendas

(none yet)
"""
SERIES_COLUMNS = ("id", "meeting", "kind", "rule", "time", "start", "end", "attendees", "milestone", "status")
OTHER_COLUMNS = ("id", "meeting", "kind", "date", "time", "attendees", "milestone", "series", "replaces", "status", "note")
BLANK = ("", "—", "-", "–")
BENIGN_PROBLEM = "the first occurrence is the next one"


def meetings_view():
    """The sibling parser, so one script defines what a series, an occurrence and a problem are."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import meetings_view as module
    finally:
        sys.path.pop(0)
    return module


def _timeline_rows(text: str) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import timeline_views
    finally:
        sys.path.pop(0)
    return timeline_views.parse(text)


def table_map(lines: list[str]) -> dict:
    """First table under each `## ` heading: {'series'|'other': {header, rows (line indexes), end}}."""
    found, heading, index = {}, "", 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("## "):
            heading = line[3:].strip().lower()
        elif line.strip().startswith("|") and heading:
            key = "series" if heading.startswith("series") else \
                "other" if heading.startswith(("one-off", "exception")) else None
            if key and key not in found and index + 1 < len(lines):
                header = [c.strip().lower() for c in split_row(line)]
                rows, scan = [], index + 2
                while scan < len(lines) and lines[scan].strip().startswith("|"):
                    rows.append(scan)
                    scan += 1
                found[key] = {"header": header, "rows": rows, "end": scan}
                index = scan - 1
        index += 1
    return found


def row_cells(line: str, header: list[str]) -> dict:
    return {name: cell.strip() for name, cell in zip(header, split_row(line))}


def set_cells(line: str, header: list[str], values: dict) -> str:
    parts = line.rstrip("\r\n").split("|")
    for name, value in values.items():
        parts[1 + header.index(name)] = f" {value} "
    return "|".join(parts) + line[len(line.rstrip("\r\n")):]


def new_row(header: list[str], values: dict, newline: str) -> str:
    return "| " + " | ".join(str(values.get(name, "")) for name in header) + " |" + newline


def next_id(lines: list[str], prefix: str) -> str:
    numbers = [int(n) for n in re.findall(r"^\|\s*" + prefix + r"-(\d+)\s*\|", "".join(lines), re.M)]
    return f"{prefix}-{max(numbers, default=0) + 1:02d}"


def time_text(parsed) -> str:
    return f"{parsed[0]}-{parsed[1]}" if parsed[1] else parsed[0]


def clean_cell(label: str, value) -> str:
    value = "" if value is None else str(value).strip()
    if "|" in value or "\n" in value:
        raise Refusal(f"{label} cannot contain '|' or a line break")
    return value


def drop_placeholder(lines: list[str], heading_start: str) -> list[str]:
    at = next((i for i, l in enumerate(lines)
               if l.startswith("## ") and l[3:].strip().lower().startswith(heading_start)), None)
    if at is None:
        return lines
    for i in range(at + 1, min(at + 6, len(lines))):
        if lines[i].strip().lower().startswith("(none yet"):
            del lines[i]
            if i < len(lines) and not lines[i].strip():
                del lines[i]
            break
    return lines


def agenda_span(lines: list[str], mid: str, when: str):
    """(section start, heading index or None, body end) for `### <mid> <when>` under `## Agendas`;
    section start is None when the file has no Agendas section."""
    at = next((i for i, l in enumerate(lines) if l.startswith("## ") and l[3:].strip().lower().startswith("agenda")), None)
    if at is None:
        return None, None, None
    end = next((i for i in range(at + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    want = f"### {mid} {when}".lower()
    head = next((i for i in range(at + 1, end) if " ".join(lines[i].split()).lower() == want), None)
    if head is None:
        return at, None, end
    stop = next((i for i in range(head + 1, end) if lines[i].startswith(("### ", "## "))), end)
    return at, head, stop


def carry_agenda(lines: list[str], mid: str, old: str, new: str, nl: str) -> bool:
    """Rename `### <mid> <old>` to `### <mid> <new>` so a moved date keeps its agenda. False when there is none."""
    _, head, _ = agenda_span(lines, mid, old)
    if head is None:
        return False
    if agenda_span(lines, mid, new)[1] is not None:
        raise Refusal(f"meetings.md already has an agenda for {mid} {new}; merge the two by hand first")
    lines[head] = f"### {mid} {new}{nl}"
    return True


def meeting_edit(text: str, args, today: str, root: Path) -> dict:
    """One meetings.md change. Returns the new text plus before/after rows, a headline for the log and
    any new overlap. Refuses a change that would leave the file with a problem it did not have."""
    mv = meetings_view()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    tabs = table_map(lines)
    series_tab, other_tab = tabs.get("series"), tabs.get("other")
    if not series_tab or not other_tab:
        raise Refusal("meetings.md needs a '## Series' table and a '## One-offs and exceptions' table")
    if [c for c in SERIES_COLUMNS if c not in series_tab["header"]] or \
            [c for c in OTHER_COLUMNS if c not in other_tab["header"]]:
        raise Refusal("meetings.md uses an older column layout; add the missing columns "
                      "(state-schema.md → Tracker conventions)")
    action = args.action
    reason = clean_cell("--reason", args.reason)
    today_date = date.fromisoformat(today)
    touched: list[date] = []
    timeline = root / "4-Trackers" / "timeline.md"

    def known_milestone(ref):
        ref = clean_cell("--milestone", ref)
        if ref in BLANK:
            return "—"
        if timeline.is_file():
            ids = {r["id"].lower() for r in _timeline_rows(read_text(timeline)) if r["has_id"]}
            if ids and ref.lower() not in ids:
                raise Refusal(f"{ref} is not a milestone in timeline.md")
        return ref

    def need(**given):
        missing = [f"--{k.replace('_', '-')}" for k, v in given.items() if not (v or "").strip()]
        if missing:
            raise Refusal(f"{action} needs " + ", ".join(missing))

    def kind_of(value):
        kind = clean_cell("--kind", value).lower()
        if kind not in mv.KINDS:
            raise Refusal("--kind must be internal or client")
        return kind

    def time_of(value):
        parsed = mv.parse_time(clean_cell("--time", value))
        if parsed is None:
            raise Refusal("--time must be HH:MM-HH:MM (local time, end after start)")
        return time_text(parsed)

    def date_of(label, value):
        day = mv.to_date(clean_cell(label, value))
        if day is None:
            raise Refusal(f"{label} must be a full date YYYY-MM-DD")
        return day

    if action == "add-series":
        need(name=args.name, kind=args.meeting_kind, rule=args.rule, time=args.time, start=args.start)
        if not mv.RULE.match(clean_cell("--rule", args.rule)):
            raise Refusal("--rule must be weekly:<day> or every-2-weeks:<day> (mon tue wed thu fri sat sun)")
        start = date_of("--start", args.start)
        end = "—"
        if args.end and args.end.strip().lower() not in ("none", "—", "-"):
            end_date = date_of("--end", args.end)
            if end_date < start:
                raise Refusal("--end is before --start")
            end = end_date.isoformat()
        new_id = next_id(lines, "S")
        values = {"id": new_id, "meeting": clean_cell("--name", args.name), "kind": kind_of(args.meeting_kind),
                  "rule": args.rule.strip().lower(), "time": time_of(args.time), "start": start.isoformat(),
                  "end": end, "attendees": clean_cell("--attendees", args.attendees) or "—",
                  "milestone": known_milestone(args.milestone), "status": "active"}
        after = new_row(series_tab["header"], values, nl)
        lines = drop_placeholder(lines, "series")
        lines.insert(table_map(lines)["series"]["end"], after)
        touched = [start]
        info = {"id": new_id, "before": "", "after": after.rstrip("\r\n"), "changes": [f"added series {new_id}"],
                "headline": f"{new_id} added: {values['meeting']} ({values['rule']} {values['time']})"}
    elif action == "add-oneoff":
        need(name=args.name, kind=args.meeting_kind, date=args.date, time=args.time)
        day = date_of("--date", args.date)
        new_id = next_id(lines, "X")
        values = {"id": new_id, "meeting": clean_cell("--name", args.name), "kind": kind_of(args.meeting_kind),
                  "date": day.isoformat(), "time": time_of(args.time),
                  "attendees": clean_cell("--attendees", args.attendees) or "—",
                  "milestone": known_milestone(args.milestone), "series": "—", "replaces": "—",
                  "status": "planned", "note": reason}
        after = new_row(other_tab["header"], values, nl)
        lines = drop_placeholder(lines, "one-off")
        lines.insert(table_map(lines)["other"]["end"], after)
        touched = [day]
        info = {"id": new_id, "before": "", "after": after.rstrip("\r\n"), "changes": [f"added meeting {new_id}"],
                "headline": f"{new_id} added: {values['meeting']} on {day} {values['time']}"}
    else:
        need(id=args.id)
        mid = args.id.strip()
        owners = [(tab, i) for tab in (series_tab, other_tab) for i in tab["rows"]
                  if row_cells(lines[i], tab["header"]).get("id", "").lower() == mid.lower()]
        if len(owners) != 1:
            raise Refusal(f"{mid} is not a meeting in meetings.md" if not owners else
                          f"{mid} appears {len(owners)} times in meetings.md; fix the duplicate by hand")
        tab, at = owners[0]
        row = row_cells(lines[at], tab["header"])
        is_series = tab is series_tab

        def exceptions_of(series_id):
            return [(i, row_cells(lines[i], other_tab["header"])) for i in other_tab["rows"]
                    if row_cells(lines[i], other_tab["header"]).get("series", "") == series_id]

        def series_dates_to(limit):
            rule, start, end = row.get("rule", ""), mv.to_date(row.get("start", "")), mv.to_date(row.get("end", ""))
            if not mv.RULE.match(rule) or start is None:
                raise Refusal(f"series {mid} is not readable; fix it by hand first")
            return mv.series_dates(rule, start, end, limit)

        if action == "edit-series":
            if not is_series:
                raise Refusal(f"{mid} is a single meeting; use move or cancel")
            need(reason=args.reason)
            fields = (("rule", args.rule), ("time", args.time), ("start", args.start), ("end", args.end),
                      ("status", args.status), ("attendees", args.attendees), ("milestone", args.milestone))
            if not any(v is not None for _, v in fields):
                raise Refusal("nothing to change: give --rule, --time, --start, --end, --status, "
                              "--attendees or --milestone")
            changes, values = [], {}
            for name, given in fields:
                if given is None:
                    continue
                if name == "rule":
                    if not mv.RULE.match(clean_cell("--rule", given)):
                        raise Refusal("--rule must be weekly:<day> or every-2-weeks:<day>")
                    value = given.strip().lower()
                elif name == "time":
                    value = time_of(given)
                elif name == "start":
                    value = date_of("--start", given).isoformat()
                elif name == "end":
                    value = "—" if given.strip().lower() in ("none", "—", "-") else date_of("--end", given).isoformat()
                elif name == "status":
                    value = clean_cell("--status", given).lower()
                    if value not in mv.SERIES_STATUS:
                        raise Refusal("--status must be active, paused or ended")
                elif name == "milestone":
                    value = known_milestone(given)
                else:
                    value = clean_cell("--attendees", given) or "—"
                if value != row.get(name, ""):
                    changes.append(f"{name} {row.get(name, '') or '—'} -> {value}")
                    values[name] = value
            if not values:
                raise Refusal(f"{mid} already has those values; nothing to change")
            new_line = set_cells(lines[at], tab["header"], values)
            info = {"id": mid, "before": lines[at].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                    "changes": changes, "headline": f"{mid} {row.get('meeting')} series changed: " + "; ".join(changes)}
            lines[at] = new_line
            touched = [d for d in (mv.to_date(values.get("start", "")), mv.to_date(values.get("end", ""))) if d]
        elif action == "move":
            need(to=args.to, reason=args.reason)
            new_day = date_of("--to", args.to)
            new_time = time_of(args.time) if args.time else None
            if not is_series:
                if args.from_date:
                    raise Refusal(f"{mid} is a single meeting; give --to (and --time), not --from")
                if row.get("status", "").lower() != "planned":
                    raise Refusal(f"{mid} is {row.get('status')}; only a planned meeting can move")
                values = {"date": new_day.isoformat()}
                if new_time:
                    values["time"] = new_time
                if values["date"] == row.get("date") and values.get("time", row.get("time")) == row.get("time"):
                    raise Refusal(f"{mid} is already on {row.get('date')} at {row.get('time')}")
                note = (row.get("note", "") + "; " if row.get("note") else "") + reason
                new_line = set_cells(lines[at], tab["header"], {**values, "note": note})
                changes = [f"date {row.get('date')} -> {values['date']}"] + (
                    [f"time {row.get('time')} -> {new_time}"] if new_time and new_time != row.get("time") else [])
                info = {"id": mid, "before": lines[at].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                        "changes": changes, "headline": f"{mid} {row.get('meeting')} moved: " + "; ".join(changes)}
                lines[at] = new_line
                touched = [new_day]
                if values["date"] != row.get("date") and carry_agenda(lines, row["id"], row.get("date", ""), values["date"], nl):
                    info["changes"].append(f"agenda moved to {values['date']}")
            else:
                need(from_date=args.from_date)
                old_day = date_of("--from", args.from_date)
                if new_day == old_day and not new_time:
                    raise Refusal("--to is the same day; give a different date or a new --time")
                generated = series_dates_to(max(old_day, new_day))
                exceptions = exceptions_of(mid)
                moved_now = next(((i, r) for i, r in exceptions if r.get("status", "").lower() in ("moved", "extra")
                                  and r.get("date") == old_day.isoformat()), None)
                occupied = {d.isoformat() for d in generated
                            if not any(r.get("replaces") == d.isoformat() for _, r in exceptions)}
                occupied |= {r.get("date") for _, r in exceptions if r.get("status", "").lower() in ("moved", "extra")}
                if new_day != old_day and new_day.isoformat() in occupied:
                    raise Refusal(f"{mid} already meets on {new_day}")
                values = {"date": new_day.isoformat()}
                if new_time:
                    values["time"] = new_time
                span = f"{mid} {old_day} -> {new_day}" + (f" at {new_time}" if new_time else "")
                headline = f"{mid} {row.get('meeting')}: {old_day} moved to {new_day}"
                if moved_now:
                    i, r = moved_now
                    note = (r.get("note", "") + "; " if r.get("note") else "") + reason
                    new_line = set_cells(lines[i], other_tab["header"], {**values, "note": note})
                    info = {"id": r.get("id"), "before": lines[i].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                            "changes": [span], "headline": headline}
                    lines[i] = new_line
                elif old_day in generated and old_day.isoformat() in occupied:
                    x_id = next_id(lines, "X")
                    after = new_row(other_tab["header"], {"id": x_id, "date": new_day.isoformat(),
                                                          "time": new_time or "", "series": mid,
                                                          "replaces": old_day.isoformat(), "status": "moved",
                                                          "note": reason}, nl)
                    lines.insert(other_tab["end"], after)
                    info = {"id": x_id, "before": "", "after": after.rstrip("\r\n"), "changes": [span],
                            "headline": headline}
                else:
                    raise Refusal(f"{mid} has no meeting on {old_day} that can move (not one of its dates, "
                                  "or already moved or cancelled)")
                touched = [old_day, new_day]
                if new_day != old_day and carry_agenda(lines, row["id"], old_day.isoformat(), new_day.isoformat(), nl):
                    info["changes"].append(f"agenda moved to {new_day}")
        elif action == "cancel":
            need(reason=args.reason)
            if not is_series:
                if args.date:
                    raise Refusal(f"{mid} is a single meeting; cancel it without --date")
                if row.get("status", "").lower() != "planned":
                    raise Refusal(f"{mid} is {row.get('status')}; only a planned meeting can be cancelled")
                note = (row.get("note", "") + "; " if row.get("note") else "") + reason
                new_line = set_cells(lines[at], tab["header"], {"status": "cancelled", "note": note})
                info = {"id": mid, "before": lines[at].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                        "changes": [f"{mid} cancelled"],
                        "headline": f"{mid} {row.get('meeting')} cancelled on {row.get('date')}"}
                lines[at] = new_line
                touched = [d for d in [mv.to_date(row.get("date", ""))] if d]
            else:
                need(date=args.date)
                day = date_of("--date", args.date)
                generated = series_dates_to(day)
                exceptions = exceptions_of(mid)
                if any(r.get("status", "").lower() == "extra" and r.get("date") == day.isoformat() for _, r in exceptions):
                    raise Refusal(f"{day} in {mid} is an extra meeting; it cannot be cancelled by script "
                                  "(edit its row by hand)")
                moved_now = next(((i, r) for i, r in exceptions if r.get("status", "").lower() == "moved"
                                  and r.get("date") == day.isoformat()), None)
                headline = f"{mid} {row.get('meeting')}: {day} cancelled"
                if moved_now:
                    i, r = moved_now
                    note = (r.get("note", "") + "; " if r.get("note") else "") + reason
                    new_line = set_cells(lines[i], other_tab["header"],
                                         {"status": "cancelled", "date": "", "time": "", "note": note})
                    info = {"id": r.get("id"), "before": lines[i].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                            "changes": [f"{mid} {day} cancelled (was moved from {r.get('replaces')})"],
                            "headline": headline}
                    lines[i] = new_line
                elif day in generated and not any(r.get("replaces") == day.isoformat() for _, r in exceptions):
                    x_id = next_id(lines, "X")
                    after = new_row(other_tab["header"], {"id": x_id, "series": mid, "replaces": day.isoformat(),
                                                          "status": "cancelled", "note": reason}, nl)
                    lines.insert(other_tab["end"], after)
                    info = {"id": x_id, "before": "", "after": after.rstrip("\r\n"),
                            "changes": [f"{mid} {day} cancelled"], "headline": headline}
                else:
                    raise Refusal(f"{mid} has no meeting on {day} to cancel (not one of its dates, "
                                  "or already moved or cancelled)")
                touched = [day]
        elif action in ("rename", "agenda"):
            if not is_series and row.get("series", "") not in BLANK:
                raise Refusal(f"{mid} is a change to series {row.get('series')}; name the series instead")
            mid = row["id"]
            if action == "rename":
                need(name=args.name, reason=args.reason)
                name = clean_cell("--name", " ".join(args.name.split()))
                if name == row.get("meeting", ""):
                    raise Refusal(f"{mid} is already called that; nothing to change")
                new_line = set_cells(lines[at], tab["header"], {"meeting": name})
                info = {"id": mid, "before": lines[at].rstrip("\r\n"), "after": new_line.rstrip("\r\n"),
                        "changes": [f"name {row.get('meeting')} -> {name}"],
                        "headline": f"{mid} renamed: {row.get('meeting')} -> {name}"}
                lines[at] = new_line
            else:
                need(date=args.date)
                if (args.items is None) == (args.none is None):
                    raise Refusal("agenda needs exactly one of --items \"A; B\" or --none \"reason\"")
                when = args.date.strip().lower()
                if when == "standing":
                    if not is_series:
                        raise Refusal(f"{mid} is a single meeting; only a series has a standing agenda")
                else:
                    day = date_of("--date", args.date)
                    when = day.isoformat()
                    if not [o for o in mv.expand(text, day, 0)["occurrences"]
                            if o["date"] == when and (o["series"] or o["id"]).lower() == mid.lower()]:
                        raise Refusal(f"{mid} has no meeting on {when} (not one of its dates, or moved or cancelled)")
                if args.none is not None:
                    reason_none = clean_cell("--none", args.none)
                    if not reason_none:
                        raise Refusal("--none needs a reason")
                    body, summary = [f"None needed: {reason_none}"], "none needed: " + reason_none
                else:
                    items = [clean_cell("an agenda item", i) for i in args.items.split(";")]
                    items = [" ".join(i.split()) for i in items if i]
                    if not items:
                        raise Refusal("--items is empty; give topics separated by ';'")
                    body, summary = [f"- {i}" for i in items], "; ".join(items)
                section, head, stop = agenda_span(lines, mid, when)
                old_body = [l.rstrip("\r\n") for l in lines[head + 1:stop] if l.strip()] if head is not None else []
                if old_body == body:
                    raise Refusal(f"{mid} {when} already has that agenda; nothing to change")
                block = [f"### {mid} {when}{nl}"] + [b + nl for b in body]
                if section is None:
                    if lines and not lines[-1].endswith(("\n", "\r")):
                        lines[-1] += nl
                    lines += ([] if lines and not lines[-1].strip() else [nl]) + [f"## Agendas{nl}", nl] + block
                elif head is not None:
                    lines[head:stop] = block + ([nl] if stop < len(lines) else [])
                else:
                    lines = drop_placeholder(lines, "agenda")
                    section, _, stop = agenda_span(lines, mid, when)
                    while stop > section + 1 and not lines[stop - 1].strip():
                        stop -= 1
                    if not lines[stop - 1].endswith(("\n", "\r")):
                        lines[stop - 1] += nl
                    lines[stop:stop] = [nl] + block + ([nl] if stop < len(lines) and lines[stop].strip() else [])
                was = "; ".join(b[2:] if b.startswith("- ") else b for b in old_body) or "not set"
                info = {"id": mid, "before": was, "after": summary,
                        "changes": [f"agenda {when}: {was} -> {summary}"],
                        "headline": f"{mid} {row.get('meeting')} agenda for {when}: {summary}", "agenda": True}
        else:
            raise Refusal(f"unknown meeting action {action}")

    for key, heading in (("series", "series"), ("other", "one-off")):
        if table_map(lines).get(key, {}).get("rows"):
            lines = drop_placeholder(lines, heading)  # "(none yet)" is no longer true
    new_text = re.sub(r"^(# Meetings) — NOT YET WRITTEN", r"\1", "".join(lines), count=1, flags=re.M)
    before_problems = set(mv.expand(text, today_date, 0)["problems"])
    added = [p for p in mv.expand(new_text, today_date, 0)["problems"]
             if p not in before_problems and BENIGN_PROBLEM not in p]
    if added:
        raise Refusal("this change would leave meetings.md with: " + " ".join(added))
    window = (max([today_date] + touched) - today_date).days
    old_overlaps = set(mv.expand(text, today_date, window)["overlaps"])
    info["conflicts"] = [o for o in mv.expand(new_text, today_date, window)["overlaps"] if o not in old_overlaps]
    info["new_text"] = new_text
    return info


def run(args) -> dict:
    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_root(start.resolve())
    if root is None:
        raise Refusal("no .familiar-project found at or above " + str(start))
    today = args.today or date.today().isoformat()
    if not valid_date(today):
        raise Refusal("--today must be YYYY-MM-DD")
    via = f" ({args.via.strip()})" if (args.via or "").strip() else ""
    result = {"kind": args.kind, "id": getattr(args, "id", None), "dry_run": args.dry_run, "root": str(root)}

    if args.kind == "milestone":
        if args.due is None and args.status is None and args.confirmed is None and args.title is None:
            raise Refusal("nothing to change: give --due, --status, --confirmed or --title")
        title = None
        if args.title is not None:
            title = clean_title(args.title)
            if not (args.reason or "").strip():
                raise Refusal("a rename needs --reason (the status log keeps old name, new name and why)")
        status = None
        if args.status is not None:
            status = args.status.strip().lower().replace("-", " ").replace("_", " ")
            status = "not applicable" if status in ("n/a", "na") else status
            if status not in STATUSES:
                raise Refusal(f"status must be one of: {', '.join(STATUSES)}")
        if args.due is not None and not valid_due(args.due.strip()):
            raise Refusal("--due must be YYYY-MM-DD, 'YYYY-MM-DD to YYYY-MM-DD', or 'No date: <reason>'")
        if args.confirmed is not None and not (valid_date(args.confirmed) or args.confirmed == "tentative"):
            raise Refusal("--confirmed must be YYYY-MM-DD or 'tentative'")
        if args.due is not None and not (args.reason or "").strip():
            raise Refusal("a date change needs --reason (the status log keeps old date, new date and why)")
        if status == "not applicable" and not (args.reason or "").strip():
            raise Refusal("'not applicable' needs --reason (why it is no longer needed goes in the status log)")
        path = root / "4-Trackers" / "timeline.md"
        if not path.is_file():
            raise Refusal("4-Trackers/timeline.md does not exist")
        text = read_text(path)
        seen = revision_of(text)
        text, stamp_added = ensure_stamp(text, today)
        new_text, before, after, old, new, track = milestone_edit(
            text, args.id, args.due.strip() if args.due is not None else None, status, args.confirmed, title)
        if before == after:
            raise Refusal(f"{args.id} already has those values; nothing to change")
        changes = []
        if new["milestone"] != old["milestone"]:
            changes.append(f"name {old['milestone']} -> {new['milestone']}")
        if new["due"] != old["due"]:
            changes.append(f"due {old['due']} -> {new['due']}")
        if new["status"] != old["status"]:
            changes.append(f"status {old['status']} -> {new['status']}")
        if new.get("confirmed") != old.get("confirmed"):
            changes.append(f"confirmed {old.get('confirmed')} -> {new.get('confirmed')}")
        kind = "deadline" if new["due"] != old["due"] else "progress"
        headline = f"{args.id} {'; '.join(changes)}"
        body = (f"{old['milestone']}. " + (f"Reason: {args.reason.strip()}. " if (args.reason or '').strip() else "")
                + f"Changed by tracker_edit.py{via} in timeline.md only; status-snapshot, tasks and downstream "
                  "milestones not yet reconciled.")
        needles = re.findall(r"\d{4}-\d{2}-\d{2}", old["due"]) if new["due"] != old["due"] else []
        if new["milestone"] != old["milestone"]:
            needles.append(old["milestone"])
        snapshot = root / "5-System" / "Memory" / "status-snapshot.md"
        tasks = root / "4-Trackers" / "tasks.md"
        context = root / "5-System" / "project-context.md"
        unprop = []
        for label, file in (("status-snapshot.md", snapshot), ("tasks.md", tasks)) + (
                (("project-context.md", context),) if new["milestone"] != old["milestone"] else ()):
            if file.is_file():
                unprop += [f"{label}: {line}" for line in references(read_text(file), needles + [args.id], "")]
        dependents = [f"{row['id']} {row['name']}" for row in
                      _dependents(text, args.id)]
        result.update({"file": "4-Trackers/timeline.md", "revision_before": seen, "revision_after": seen + 1,
                       "before": before, "after": after, "changes": changes, "log": f"[{today}] {kind} | {headline}",
                       "unpropagated": unprop[:12], "dependents": dependents, "closes_with_open_dependents":
                       bool(dependents) and new["status"] in ("done", "not applicable")})
        write_target, write_text = path, bump(new_text, today)
    elif args.kind == "meeting":
        path = root / "4-Trackers" / "meetings.md"
        if path.is_file():
            text = read_text(path)
        elif args.action.startswith("add-"):
            text = MEETINGS_STUB
        else:
            raise Refusal("4-Trackers/meetings.md does not exist; only add-series and add-oneoff can start one")
        seen = revision_of(text)
        text, stamp_added = ensure_stamp(text, today)
        info = meeting_edit(text, args, today, root)
        kind, headline, track = "meeting", info["headline"], ""
        body = ((f"Reason: {args.reason.strip()}. " if (args.reason or "").strip() else "")
                + (f"Previous agenda: {info['before']}. " if info.get("agenda") else "")
                + f"Changed by tracker_edit.py{via} in meetings.md only."
                + ("" if info.get("agenda") else " Update your own calendar (Outlook) to match."))
        result.update({"id": info["id"], "file": "4-Trackers/meetings.md", "revision_before": seen,
                       "revision_after": seen + 1, "before": info["before"], "after": info["after"],
                       "changes": info["changes"], "log": f"[{today}] {kind} | {headline}",
                       "conflicts": info["conflicts"]})
        if not info.get("agenda"):
            result["calendar_note"] = "Update your own calendar: " + headline
        write_target, write_text = path, bump(info["new_text"], today)
    else:
        if sum(bool(x) for x in (args.done, args.reopen, args.due, args.no_date, args.title is not None)) != 1:
            raise Refusal("give exactly one of --done, --reopen, --due, --no-date, --title")
        if args.due and not valid_date(args.due):
            raise Refusal("--due must be a full date YYYY-MM-DD")
        if (args.due or args.no_date) and not (args.reason or "").strip():
            raise Refusal("a date change needs --reason (the status log keeps old date, new date and why)")
        title = clean_title(args.title) if args.title is not None else None
        if title is not None and not (args.reason or "").strip():
            raise Refusal("a rename needs --reason (the status log keeps old name, new name and why)")
        path = root / "4-Trackers" / "tasks.md"
        if not path.is_file():
            raise Refusal("4-Trackers/tasks.md does not exist")
        text = read_text(path)
        seen = revision_of(text)
        text, stamp_added = ensure_stamp(text, today)
        new_text, before, after, was_checked, moved, old_title = task_edit(
            text, args.id, today, args.done, args.reopen, args.due, args.no_date, title)
        what = ("done" if args.done else "reopened" if args.reopen else f"renamed: {old_title} -> {title}"
                if title is not None else f"no date ({args.no_date})" if args.no_date else f"date -> {args.due}")
        kind = "deadline" if (args.due or args.no_date) else "progress"
        headline = f"{args.id} {what}"
        body = ((f"Reason: {args.reason.strip()}. " if (args.reason or '').strip() else "")
                + f"Changed by tracker_edit.py{via} in tasks.md only; status-snapshot and timeline not yet reconciled.")
        snapshot = root / "5-System" / "Memory" / "status-snapshot.md"
        timeline = root / "4-Trackers" / "timeline.md"
        unprop = []
        old_dates = re.findall(r"\d{4}-\d{2}-\d{2}", before.split("\n", 1)[0]) if (args.due or args.no_date) else []
        for label, file in (("status-snapshot.md", snapshot), ("timeline.md", timeline)):
            if file.is_file():
                unprop += [f"{label}: {line}" for line in references(read_text(file), [args.id] + old_dates, "")]
        if old_title:
            # A blocker may name this task by its title in words; those lines still say the old one.
            own = set(after.splitlines())
            unprop += [f"tasks.md: {line}" for line in references(new_text, [old_title.rstrip(".")], "")
                       if line not in {o.strip()[:160] for o in own}]
        dependents = []
        if args.done:
            dep = re.compile(r"blocked by:[^\n]*\b" + re.escape(args.id) + r"\b", re.IGNORECASE)
            lines = new_text.splitlines()
            for s, e in task_blocks(lines):
                block = "\n".join(lines[s:e])
                if dep.search(block) and not TASK_LINE.match(lines[s]).group(2) in ("x", "X"):
                    dependents.append(lines[s].strip()[:200])
        result.update({"file": "4-Trackers/tasks.md", "revision_before": seen, "revision_after": seen + 1,
                       "before": before, "after": after, "changes": [what], "moved_to_done": moved,
                       "log": f"[{today}] {kind} | {headline}", "unpropagated": unprop[:12],
                       "unblocked_or_dependent": dependents})
        write_target, write_text = path, bump(new_text, today)
        track = ""

    result["stamp_added"] = stamp_added
    if args.expect_revision is not None and args.expect_revision != seen:
        raise Refusal(f"{result['file']} is at revision {seen}, not {args.expect_revision}: it changed since the "
                      "preview. Reread and preview again.")
    if args.dry_run:
        return result
    # CORE-STALE-WRITE: reread immediately before replacing (a new meetings.md must still not exist).
    if (revision_of(read_text(write_target)) if write_target.is_file() else 0) != seen:
        raise Refusal(f"{result['file']} changed while this edit was prepared (revision is no longer {seen}); nothing written")
    atomic_write(write_target, write_text)
    log_path, entry = log_entry(root, today, kind, headline, body, track)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        append_log(log_path, entry)
        result["log_written"] = True
    except OSError as err:
        result["log_written"] = False
        result["warning"] = f"{result['file']} was changed but the status-log entry failed ({err}); add it by hand"
    result["views"] = regenerate_views(root, "meetings_view.py" if args.kind == "meeting" else "timeline_views.py")
    return result


def _dependents(timeline_text: str, mid: str) -> list[dict]:
    """Open milestones that list `mid` in Depends on (reported, never moved)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import timeline_views  # sibling script; same parser, one definition of a milestone row
    finally:
        sys.path.pop(0)
    rows = timeline_views.parse(timeline_text)
    return [r for r in rows if timeline_views.is_open(r) and mid.lower() in [d.lower() for d in r["depends"]]]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="kind", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--id", required=True)
    common.add_argument("--reason")
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--expect-revision", type=int)
    common.add_argument("--json", action="store_true")
    common.add_argument("--via", help="who asked, noted in the status-log entry (the fleet hub passes 'fleet hub')")
    common.add_argument("--root")
    common.add_argument("--today", help="YYYY-MM-DD, for testing")
    m = sub.add_parser("milestone", parents=[common])
    m.add_argument("--due")
    m.add_argument("--status")
    m.add_argument("--confirmed")
    m.add_argument("--title", help="rename the milestone (needs --reason)")
    t = sub.add_parser("task", parents=[common])
    t.add_argument("--done", action="store_true")
    t.add_argument("--reopen", action="store_true")
    t.add_argument("--due")
    t.add_argument("--no-date", metavar="REASON")
    t.add_argument("--title", help="rename the task (needs --reason)")
    mt = sub.add_parser("meeting", help="add, move, cancel or edit a meeting in meetings.md")
    acts = mt.add_subparsers(dest="action", required=True)
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--reason")
    flags.add_argument("--dry-run", action="store_true")
    flags.add_argument("--expect-revision", type=int)
    flags.add_argument("--json", action="store_true")
    flags.add_argument("--via", help="who asked, noted in the status-log entry (the fleet hub passes 'fleet hub')")
    flags.add_argument("--root")
    flags.add_argument("--today", help="YYYY-MM-DD, for testing")
    a = acts.add_parser("add-series", parents=[flags])
    for name in ("name", "rule", "time", "start", "end", "attendees", "milestone"):
        a.add_argument("--" + name)
    a.add_argument("--kind", dest="meeting_kind")  # not `kind`: that is the sub-command's own dest
    o = acts.add_parser("add-oneoff", parents=[flags])
    for name in ("name", "date", "time", "attendees", "milestone"):
        o.add_argument("--" + name)
    o.add_argument("--kind", dest="meeting_kind")
    mo = acts.add_parser("move", parents=[flags])
    mo.add_argument("--id", required=True)
    mo.add_argument("--from", dest="from_date", help="the series date being moved (not for a single meeting)")
    mo.add_argument("--to")
    mo.add_argument("--time")
    ca = acts.add_parser("cancel", parents=[flags])
    ca.add_argument("--id", required=True)
    ca.add_argument("--date", help="the series date to cancel (not for a single meeting)")
    es = acts.add_parser("edit-series", parents=[flags])
    es.add_argument("--id", required=True)
    for name in ("rule", "time", "start", "end", "status", "attendees", "milestone"):
        es.add_argument("--" + name)
    rn = acts.add_parser("rename", parents=[flags])
    rn.add_argument("--id", required=True)
    rn.add_argument("--name")
    ag = acts.add_parser("agenda", parents=[flags], help="set one date's agenda, or a series' standing agenda")
    ag.add_argument("--id", required=True)
    ag.add_argument("--date", help="YYYY-MM-DD (the meeting's date as shown) or 'standing'")
    ag.add_argument("--items", help="topics separated by ';'")
    ag.add_argument("--none", help="no agenda is needed, and why")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    try:
        result = run(args)
    except Refusal as err:
        if args.json:
            print(json.dumps({"ok": False, "error": str(err)}, indent=2))
        else:
            print("REFUSED: " + str(err), file=sys.stderr)
        raise SystemExit(1)
    result["ok"] = True
    if args.json:
        print(json.dumps(result, indent=2))
        return
    verb = "would change" if args.dry_run else "changed"
    print(f"{verb} {result['file']} (revision {result['revision_before']} -> {result['revision_after']})")
    print("  before: " + result["before"].split("\n", 1)[0])
    print("  after:  " + result["after"].split("\n", 1)[0])
    print("  log:    " + result["log"])
    for item in result.get("conflicts") or []:
        print(f"  conflict: {item}")
    if result.get("calendar_note"):
        print("  reminder: " + result["calendar_note"])
    if result.get("stamp_added"):
        print("  note:   the file had no revision stamp; its schema header lines are added (revision 1)")
    for key in ("dependents", "unblocked_or_dependent", "unpropagated"):
        for item in result.get(key) or []:
            print(f"  {key}: {item}")
    if result.get("warning"):
        print("  WARNING: " + result["warning"])
    if "views" in result:
        print("  views:  " + result["views"])


if __name__ == "__main__":
    main()
