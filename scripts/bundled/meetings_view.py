#!/usr/bin/env python3
"""meetings_view.py — read `4-Trackers/meetings.md` and render its meetings as dated occurrences.

    python .familiar/scripts/meetings_view.py              # write 4-Trackers/_views/meetings-next-weeks.md
    python .familiar/scripts/meetings_view.py --stdout     # print, write nothing
    python .familiar/scripts/meetings_view.py --json       # machine-readable, write nothing
    python .familiar/scripts/meetings_view.py --json --window 42   # occurrences reach 42 days ahead
    python .familiar/scripts/meetings_view.py --today 2026-10-15

`meetings.md` is the only place a project's planned internal and client meetings are edited. It has
two tables: Series (one row per recurring meeting, with a rule) and One-offs and exceptions (single
meetings, plus per-date changes to a series: moved, cancelled, extra). This script expands the rules
into dated occurrences, applies the exceptions, and reports problems; it never changes `meetings.md`.
A project without `meetings.md` is valid and gets no view.

An optional `## Agendas` section holds each meeting's topic list under `### <ID> <date>` (or
`### <S-ID> standing`, the list a series date uses when it has none of its own); every occurrence carries
its `agenda` (`items`, `none` reason, `from` dated or standing) or null.

Rules (`state-schema.md` → Tracker conventions): `weekly:<day>` and `every-2-weeks:<day>`, day one of
mon tue wed thu fri sat sun. The first occurrence is the first named day on or after `Start`; an
every-2-weeks series counts from it. `End` is inclusive. Times are local `HH:MM-HH:MM`; there is no
timezone field. Anything this script cannot read is listed as a problem, never guessed.
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

ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
REVISION = re.compile(r"<!--\s*revision:\s*(\d+)\s*-->")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
RULE = re.compile(r"^(weekly|every-2-weeks):(mon|tue|wed|thu|fri|sat|sun)$", re.IGNORECASE)
TIME = re.compile(r"^(\d{1,2}):(\d{2})(?:\s*-\s*(\d{1,2}):(\d{2}))?$")
STAMP = "<!-- generated from meetings.md revision {rev} on {today} by .familiar/scripts/meetings_view.py; do not edit -- edit meetings.md and regenerate -->"
KINDS = ("internal", "client")
EXCEPTION_STATUS = ("moved", "cancelled", "extra")
ONEOFF_STATUS = ("planned", "done", "cancelled")
SERIES_STATUS = ("active", "paused", "ended")
WINDOW_DAYS = 28
BLANK = ("", "—", "-", "–")
AGENDA_HEADING = re.compile(r"^###\s+([A-Za-z]-\d+)\s+(\d{4}-\d{2}-\d{2}|standing)\s*$", re.IGNORECASE)
NONE_NEEDED = re.compile(r"^none needed:\s*(.*)$", re.IGNORECASE)


def find_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".familiar-project").is_file():
            return folder
    return None


def to_date(text: str):
    m = ISO.match((text or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_time(text: str):
    """(start 'HH:MM', end 'HH:MM' or '') or None when unreadable."""
    m = TIME.match((text or "").strip())
    if not m:
        return None
    sh, sm, eh, em = m.groups()
    if int(sh) > 23 or int(sm) > 59:
        return None
    start = f"{int(sh):02d}:{sm}"
    if eh is None:
        return start, ""
    if int(eh) > 23 or int(em) > 59:
        return None
    end = f"{int(eh):02d}:{em}"
    return (start, end) if end > start else None


def split_people(text: str) -> list[str]:
    text = (text or "").strip()
    if text in BLANK:
        return []
    parts = re.split(r";" if ";" in text else ",", text)
    return [p.strip() for p in parts if p.strip()]


def tables(text: str) -> dict[str, list[dict]]:
    """Rows of each markdown table, keyed by the lower-cased `##` heading above it. A row is a dict of
    lower-cased header -> cell, plus `_line`. Rows starting `(example)` are skipped."""
    out: dict[str, list[dict]] = {}
    heading, header = "", None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            heading, header = stripped[3:].strip().lower(), None
            continue
        if not stripped.startswith("|"):
            header = None if header is not None and not stripped else header
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        if re.match(r"^\W*\(example\)", row.get("meeting", ""), re.IGNORECASE):
            continue
        row["_line"] = number
        out.setdefault(heading, []).append(row)
    return out


def sections(text: str) -> tuple[list[dict], list[dict]]:
    found = tables(text)
    series = next((rows for key, rows in found.items() if key.startswith("series")), [])
    other = next((rows for key, rows in found.items() if key.startswith("one-off") or key.startswith("exception")), [])
    return series, other


def agendas(text: str) -> tuple[dict, list[str]]:
    """{(ID lower-cased, 'YYYY-MM-DD' | 'standing'): {'items', 'none', 'line'}} from `## Agendas`, plus problems.
    A body is `- topic` bullets, or one `None needed: <reason>` line; anything else is ignored."""
    found, problems, inside, current = {}, [], False, None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            inside, current = stripped[3:].strip().lower().startswith("agenda"), None
            continue
        if not inside:
            continue
        if stripped.startswith("###"):
            m = AGENDA_HEADING.match(stripped)
            if not m:
                problems.append(f"Agenda heading on line {number} (`{stripped}`) is not `### <ID> <YYYY-MM-DD>` or "
                                "`### <S-ID> standing`.")
                current = None
                continue
            key = (m.group(1).lower(), m.group(2).lower())
            if key in found:
                problems.append(f"Agenda `{stripped}` appears more than once in meetings.md.")
            current = found[key] = {"items": [], "none": "", "line": number, "id": m.group(1).upper()}
            continue
        if current is None:
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        none = NONE_NEEDED.match(stripped)
        if bullet and bullet.group(1).strip():
            current["items"].append(bullet.group(1).strip())
        elif none:
            current["none"] = none.group(1).strip() or "not given"
    return found, problems


def series_dates(rule: str, start: date, end: date | None, limit: date) -> list[date]:
    m = RULE.match(rule.strip())
    step = 7 if m.group(1).lower() == "weekly" else 14
    first = start + timedelta(days=(DAYS.index(m.group(2).lower()) - start.weekday()) % 7)
    stop = min(end, limit) if end else limit
    out, d = [], first
    while d <= stop:
        out.append(d)
        d += timedelta(days=step)
    return out


def expand(text: str, today: date, window: int = WINDOW_DAYS) -> dict:
    """Occurrences from `today` through `today + window`, the series, and every problem found."""
    series_rows, other_rows = sections(text)
    horizon = today + timedelta(days=max(window, 0))
    problems: list[str] = []
    seen_ids: set[str] = set()
    series: dict[str, dict] = {}

    def claim(row, label):
        mid = row.get("id", "").strip()
        if not mid:
            problems.append(f"{label} on line {row['_line']} has no ID; give it one (S-01, X-01).")
        elif mid in seen_ids:
            problems.append(f"ID {mid} is used more than once in meetings.md; IDs are never reused.")
        seen_ids.add(mid)
        return mid

    for row in series_rows:
        sid = claim(row, "A series")
        name = row.get("meeting", "") or sid
        kind = row.get("kind", "").lower()
        when, hours, start = row.get("rule", ""), parse_time(row.get("time", "")), to_date(row.get("start", ""))
        end_text = row.get("end", "")
        end = to_date(end_text)
        status = (row.get("status", "") or "active").lower()
        bad = []
        if kind not in KINDS:
            bad.append(f"Kind `{row.get('kind', '')}` is not internal or client")
        if not RULE.match(when.strip()):
            bad.append(f"Rule `{when}` is not weekly:<day> or every-2-weeks:<day>")
        if hours is None:
            bad.append(f"Time `{row.get('time', '')}` is not HH:MM-HH:MM")
        if start is None:
            bad.append(f"Start `{row.get('start', '')}` is not a full YYYY-MM-DD date")
        if end_text not in BLANK and end is None:
            bad.append(f"End `{end_text}` is not a full YYYY-MM-DD date")
        if end and start and end < start:
            bad.append("End is before Start")
        if status not in SERIES_STATUS:
            bad.append(f"Status `{status}` is not active, paused or ended")
        if bad:
            problems.append(f"Series {sid} ({name}) is skipped: " + "; ".join(bad) + ".")
            continue
        m = RULE.match(when.strip())
        if start.weekday() != DAYS.index(m.group(2).lower()):
            problems.append(f"Series {sid} ({name}) starts {start}, not a {m.group(2).lower()}; the first "
                            "occurrence is the next one.")
        series[sid] = {"id": sid, "meeting": name, "kind": kind, "rule": when.strip().lower(), "start": start,
                       "end": end, "time": hours, "attendees": split_people(row.get("attendees", "")),
                       "milestone": row.get("milestone", "").strip(), "status": status}

    exceptions: dict[tuple[str, date], dict] = {}
    extras: list[dict] = []
    oneoffs: list[dict] = []
    for row in other_rows:
        xid = claim(row, "A meeting row")
        parent = row.get("series", "").strip()
        status = row.get("status", "").lower()
        new_date, hours = to_date(row.get("date", "")), parse_time(row.get("time", ""))
        replaces_text = row.get("replaces", "").strip()
        replaces = to_date(replaces_text)
        if parent in BLANK:
            kind, name = row.get("kind", "").lower(), row.get("meeting", "") or xid
            bad = []
            if status not in ONEOFF_STATUS:
                bad.append(f"Status `{status}` is not planned, done or cancelled")
            if kind not in KINDS:
                bad.append(f"Kind `{row.get('kind', '')}` is not internal or client")
            if new_date is None:
                bad.append(f"Date `{row.get('date', '')}` is not a full YYYY-MM-DD date")
            if hours is None:
                bad.append(f"Time `{row.get('time', '')}` is not HH:MM-HH:MM")
            if bad:
                problems.append(f"Meeting {xid} ({name}) is skipped: " + "; ".join(bad) + ".")
                continue
            oneoffs.append({"id": xid, "meeting": name, "kind": kind, "date": new_date, "time": hours,
                            "attendees": split_people(row.get("attendees", "")),
                            "milestone": row.get("milestone", "").strip(), "status": status,
                            "note": row.get("note", ""), "source": "one-off"})
            continue
        base = series.get(parent)
        if status not in EXCEPTION_STATUS:
            problems.append(f"Exception {xid} is skipped: Status `{status}` is not moved, cancelled or extra.")
            continue
        if base is None:
            problems.append(f"Exception {xid} names series {parent}, which is not a readable series here.")
            continue
        if status in ("moved", "cancelled") and replaces is None:
            problems.append(f"Exception {xid} ({status}) needs the original date in Replaces.")
            continue
        if status in ("moved", "extra") and (new_date is None or (row.get("time", "") not in BLANK and hours is None)):
            problems.append(f"Exception {xid} ({status}) needs a full Date and, if given, a readable Time.")
            continue
        entry = {"id": xid, "date": new_date, "time": hours or base["time"], "replaces": replaces,
                 "status": status, "note": row.get("note", ""),
                 "attendees": split_people(row.get("attendees", "")) or base["attendees"],
                 "milestone": row.get("milestone", "").strip() or base["milestone"]}
        if status == "extra":
            extras.append({**entry, "series": parent})
        else:
            if (parent, replaces) in exceptions:
                problems.append(f"Exception {xid} replaces {replaces} in {parent}, which another row already changes.")
                continue
            exceptions[(parent, replaces)] = entry

    occurrences: list[dict] = []

    def add(item, day):
        if today <= day <= horizon:
            occurrences.append({"date": day.isoformat(), "start": item["time"][0], "end": item["time"][1],
                                "meeting": item["meeting"], "kind": item["kind"], "attendees": item["attendees"],
                                "milestone": item["milestone"], "id": item["id"], "series": item.get("series", ""),
                                "source": item["source"], "moved_from": item.get("moved_from", ""),
                                "note": item.get("note", "")})

    for sid, base in series.items():
        reach = max([horizon] + [d for (s, _), e in exceptions.items() if s == sid for d in [e["replaces"]]])
        generated = series_dates(base["rule"], base["start"], base["end"], reach)
        for (s, old), exc in exceptions.items():
            if s == sid and old not in generated:
                problems.append(f"Exception {exc['id']} changes {old} in {sid}, which is not one of its occurrences.")
        if base["status"] == "paused":
            continue
        for day in generated:
            exc = exceptions.get((sid, day))
            if exc is None:
                add({**base, "id": sid, "series": sid, "source": "series"}, day)
            elif exc["status"] == "moved":
                add({**base, "id": exc["id"], "series": sid, "source": "moved", "time": exc["time"],
                     "attendees": exc["attendees"], "milestone": exc["milestone"], "note": exc["note"],
                     "moved_from": day.isoformat()}, exc["date"])
    for exc in extras:
        base = series.get(exc["series"])
        if base and base["status"] != "paused":
            add({**base, "id": exc["id"], "series": exc["series"], "source": "extra", "time": exc["time"],
                 "attendees": exc["attendees"], "milestone": exc["milestone"], "note": exc["note"]}, exc["date"])
    for one in oneoffs:
        if one["status"] != "cancelled":
            add({**one, "series": ""}, one["date"])

    lists, agenda_problems = agendas(text)
    problems += agenda_problems
    oneoff_ids = {o["id"].lower() for o in oneoffs}
    for (ref, when), entry in lists.items():
        if ref not in {s.lower() for s in series} and ref not in oneoff_ids:
            problems.append(f"Agenda `### {entry['id']} {when}` names no series or one-off meeting in this file.")
        elif when == "standing" and ref in oneoff_ids:
            problems.append(f"Agenda `### {entry['id']} standing`: only a series has a standing agenda.")
    for o in occurrences:
        ref = (o["series"] or o["id"]).lower()
        dated, standing = lists.get((ref, o["date"])), lists.get((ref, "standing")) if o["series"] else None
        chosen = dated or standing
        o["agenda"] = None if chosen is None else {"items": chosen["items"], "none": chosen["none"],
                                                   "from": "dated" if dated else "standing"}
    occurrences.sort(key=lambda o: (o["date"], o["start"], o["meeting"]))
    overlaps = []
    for i, a in enumerate(occurrences):
        for b in occurrences[i + 1:]:
            if b["date"] != a["date"]:
                break
            a_end, b_end = a["end"] or a["start"], b["end"] or b["start"]
            if b["start"] < a_end and a["start"] < b_end:
                overlaps.append(f"{a['date']}: {a['meeting']} ({a['start']}-{a['end']}) overlaps "
                                f"{b['meeting']} ({b['start']}-{b['end']}).")
    return {"occurrences": occurrences, "series": len(series), "overlaps": overlaps, "problems": problems}


def render(result: dict, rev: str, today: date, window: int) -> str:
    out = [STAMP.format(rev=rev, today=today), f"# Meetings — {today} to {today + timedelta(days=window)}", ""]
    if not result["occurrences"]:
        out.append("No meetings in this window.")
    day = None
    for o in result["occurrences"]:
        if o["date"] != day:
            day = o["date"]
            out += ["", f"## {day} ({DAYS[date.fromisoformat(day).weekday()].capitalize()})", ""]
        hours = f"{o['start']}-{o['end']}" if o["end"] else o["start"]
        who = f" — {', '.join(o['attendees'])}" if o["attendees"] else ""
        extra = []
        if o["source"] == "moved":
            extra.append(f"moved from {o['moved_from']}")
        if o["source"] == "extra":
            extra.append("extra")
        if o["milestone"] not in BLANK:
            extra.append(o["milestone"])
        note = f" ({'; '.join(extra)})" if extra else ""
        out.append(f"- {hours} **{o['meeting']}** [{o['kind']}]{who}{note}")
        if o.get("agenda"):
            a = o["agenda"]
            label = "Agenda" + (" (standing)" if a["from"] == "standing" else "")
            out.append(f"  - {label}: " + ("none needed — " + a["none"] if a["none"] else "; ".join(a["items"]) or "empty"))
    out += ["", "## Overlaps", ""] + ([f"- {p}" for p in result["overlaps"]] or ["None."])
    out += ["", "## Problems", ""] + ([f"- {p}" for p in result["problems"]] or ["None found."])
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stdout", action="store_true", help="print the view instead of writing it")
    ap.add_argument("--json", action="store_true", help="print occurrences and problems as JSON, write nothing")
    ap.add_argument("--today", help="YYYY-MM-DD, for testing")
    ap.add_argument("--root", help="project folder; defaults to the nearest parent with .familiar-project")
    ap.add_argument("--window", type=int, default=WINDOW_DAYS,
                    help=f"days ahead to expand (default {WINDOW_DAYS})")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    start = Path(args.root) if args.root else Path(__file__).resolve().parent
    root = find_root(start.resolve())
    if root is None:
        raise SystemExit("ERROR: no .familiar-project found at or above " + str(start))
    today = date.fromisoformat(args.today) if args.today else date.today()
    window = max(args.window, 0)
    path = root / "4-Trackers" / "meetings.md"
    if not path.is_file():
        if args.json:
            print(json.dumps({"root": str(root), "today": today.isoformat(), "present": False}, indent=2))
        else:
            print("no 4-Trackers/meetings.md in this project; nothing to do")
        return
    text = path.read_text(encoding="utf-8")
    rev_match = REVISION.search(text)
    rev = rev_match.group(1) if rev_match else "unknown"
    result = expand(text, today, window)
    if args.json:
        print(json.dumps({"root": str(root), "today": today.isoformat(), "present": True, "revision": rev,
                          "window": window, **result}, indent=2))
        return
    body = render(result, rev, today, window)
    if args.stdout:
        print(body)
        return
    folder = root / "4-Trackers" / "_views"
    folder.mkdir(exist_ok=True)
    (folder / "meetings-next-weeks.md").write_text(body, encoding="utf-8", newline="\n")
    print(f"view written to 4-Trackers/_views/meetings-next-weeks.md from meetings.md revision {rev}: "
          f"{result['series']} series, {len(result['occurrences'])} occurrence(s) in {window} days, "
          f"{len(result['problems'])} problem(s)")


if __name__ == "__main__":
    main()
