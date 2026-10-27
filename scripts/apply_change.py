#!/usr/bin/env python3
"""apply_change.py — the hub's one write path into registered projects: a previewed, confirmed batch of
milestone, task and meeting changes, each made by that project's own `tracker_edit.py`.

    python scripts/apply_change.py preview              # read _overview/pending-changes.txt, dry-run every line,
                                                        # print the batch, write _overview/pending-preview.json
    python scripts/apply_change.py apply --yes          # apply exactly what the preview showed, nothing else

`preview` changes nothing in any project. `apply` refuses without `--yes`, without a preview, if the
pending file changed since the preview, and per line if that project's file moved on since the preview
(the script's own revision check). It never edits a project file itself: every change goes through
`tracker_edit.py`, which checks the revision, replaces the file atomically, logs old -> new with the
reason, and regenerates the timeline views. A project that has no `tracker_edit.py` yet is edited with
the hub's bundled copy and the line says so.

A change line is one row, pipe-separated (the brief page's "Pending changes" copy writes the same):

    <project> | milestone M-04 | due 2026-11-03 | reason: client moved it
    <project> | milestone M-07 | status not-applicable | reason: dropped from SOW
    <project> | milestone M-02 | confirmed 2026-10-15
    <project> | task T-12 | done
    <project> | task T-12 | reopen
    <project> | task T-12 | due 2026-11-03 | reason: waiting on data
    <project> | task T-12 | no-date waiting on the client | reason: ...
    <project> | task T-12 | title New wording | reason: scope grew
    <project> | milestone M-04 | title New wording | reason: ...

A milestone's `status not-applicable` needs a reason, like a date move, and so does a rename. A reason cannot
contain `|`.

Meetings are edit-only, like milestones: the hub can move or cancel one date and end or change a series, and
can never create a meeting (adding one happens in a project session). A series is S-nn, a single meeting X-nn;
every meeting line needs a reason:

    <project> | meeting S-01 | move 2026-10-20 to 2026-10-21 [14:00-14:30] | reason: out of office
    <project> | meeting S-01 | cancel 2026-10-27 | reason: holiday
    <project> | meeting S-01 | end 2027-02-02 | reason: wraps early      (also: status paused|active|ended, time HH:MM-HH:MM, rule weekly:<day>)
    <project> | meeting X-01 | move 2026-11-06 [13:00-14:00] | reason: client asked
    <project> | meeting X-01 | cancel | reason: no longer needed
    <project> | meeting S-01 | title New name | reason: ...          (renames the series, or the single meeting)

Notes only: nothing here touches a calendar, so every meeting change ends with a reminder to update your own.

An agenda is a short topic list for one meeting date (as the brief shows it, after any move), or a series'
standing list. It is not a change of plan, so it needs no reason; topics are separated by `;`:

    <project> | meeting S-01 | agenda 2026-10-28 | items: Action items; Timeline check
    <project> | meeting S-01 | agenda standing | items: Action items
    <project> | meeting X-01 | agenda 2026-11-04 | none: internal walkthrough only

"Remind me on" is a personal reminder, not a fact about the project's meeting: it is written to this hub's
`_overview/agenda-snoozes.md` only, never to the project (the preview marks it hub-local):

    <project> | meeting S-01 | remind 2026-10-27 on 2026-10-28

Every applied change also gets one readable line in `_overview/change-log.md`.

Blank lines, lines starting with # and the page's "Apply these changes" heading line are ignored. A project is matched by its registry name (exact,
then a unique part of it), only among the projects this hub covers (`hub-settings.md` -> `categories`,
included rows of `~/.familiar/fleet.md`); one outside that scope is reported as not found, never read.
Standard library only.
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_overview as fo  # noqa: E402

OUT = fo.HUB / "_overview"
PENDING = "pending-changes.txt"
PREVIEW = "pending-preview.json"
SNOOZES = "agenda-snoozes.md"
CHANGE_LOG = "change-log.md"
ACTIONS = ("due", "status", "confirmed", "done", "reopen", "no-date", "title")
MEETING_ACTIONS = ("move", "cancel", "end", "status", "time", "rule", "title", "agenda", "remind")
SERIES_EDITS = ("end", "status", "time", "rule")
DATE = r"\d{4}-\d{2}-\d{2}"
MEETING_ID = re.compile(r"^[SX]-\d+$", re.IGNORECASE)


class BadLine(Exception):
    pass


def meeting_args(changes: dict, mid: str, agenda: dict) -> dict:
    """The tracker_edit.py `meeting` action and flags one meeting line stands for. Only move, cancel,
    edit-series, rename and agenda exist here: the hub has no way to add a meeting. `remind` is hub-local and
    never reaches tracker_edit.py. `on` is what the line is about (two lines about the same date in one batch
    are a mistake; two dates of one series are not, nor a rename beside a date change)."""
    if not MEETING_ID.match(mid):
        raise BadLine(f"'{mid}' is not a meeting ID; a series is S-nn and a single meeting X-nn")
    edits = [w for w in changes if w in SERIES_EDITS]
    if sum([bool(edits)] + [w in changes for w in ("move", "cancel", "title", "agenda", "remind")]) != 1:
        raise BadLine("a meeting line takes one of: move, cancel, title, agenda, remind, "
                      "or series edits (end, status, time, rule)")
    if agenda and "agenda" not in changes:
        raise BadLine("items: and none: belong to an agenda line ('agenda YYYY-MM-DD | items: A; B')")
    series = mid.upper().startswith("S")
    if "title" in changes:
        return {"action": "rename", "flags": ["--name", changes["title"]], "on": "title", "lands": ""}
    if "agenda" in changes:
        when = changes["agenda"].lower()
        if when != "standing" and not re.match(rf"^{DATE}$", when):
            raise BadLine("an agenda is for one date or the standing list: 'agenda YYYY-MM-DD' or 'agenda standing'")
        if len(agenda) != 1:
            raise BadLine("an agenda line takes one of '| items: A; B' or '| none: <reason>'")
        flags = ["--date", when] + (["--items", agenda["items"]] if "items" in agenda else ["--none", agenda["none"]])
        return {"action": "agenda", "flags": flags, "on": "agenda " + when, "lands": ""}
    if "remind" in changes:
        m = re.match(rf"^({DATE}) on ({DATE})$", changes["remind"])
        if not m:
            raise BadLine("a reminder reads 'remind YYYY-MM-DD on YYYY-MM-DD' (remind on the first date about "
                          "the meeting on the second)")
        if m.group(1) > m.group(2):
            raise BadLine(f"a reminder on {m.group(1)} would come after the meeting on {m.group(2)}")
        return {"action": "remind", "flags": [], "on": "remind " + m.group(2), "lands": m.group(1),
                "remind": m.group(1), "date": m.group(2), "hub_local": True}
    if "move" in changes:
        value = changes["move"]
        if series:
            m = re.match(rf"^({DATE}) to ({DATE})(?: (\S+))?$", value)
            if not m:
                raise BadLine("a series date moves as 'move YYYY-MM-DD to YYYY-MM-DD [HH:MM-HH:MM]'")
            flags = ["--from", m.group(1), "--to", m.group(2)] + (["--time", m.group(3)] if m.group(3) else [])
            return {"action": "move", "flags": flags, "on": m.group(1), "lands": m.group(2)}
        m = re.match(rf"^({DATE})(?: (\S+))?$", value)
        if not m:
            raise BadLine("a single meeting moves as 'move YYYY-MM-DD [HH:MM-HH:MM]'")
        return {"action": "move", "flags": ["--to", m.group(1)] + (["--time", m.group(2)] if m.group(2) else []),
                "on": "", "lands": m.group(1)}
    if "cancel" in changes:
        value = changes["cancel"]
        if series:
            if value is True or not re.match(rf"^{DATE}$", value):
                raise BadLine("a series date is cancelled as 'cancel YYYY-MM-DD'")
            return {"action": "cancel", "flags": ["--date", value], "on": value, "lands": ""}
        if value is not True:
            raise BadLine("a single meeting is cancelled as 'cancel' with no date")
        return {"action": "cancel", "flags": [], "on": "", "lands": ""}
    if not series:
        raise BadLine("end, status, time and rule edit a series (S-nn); a single meeting can only move or cancel")
    return {"action": "edit-series", "flags": [f for w in edits for f in (f"--{w}", changes[w])], "on": "", "lands": ""}


def parse_line(line: str) -> dict:
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        raise BadLine("expected: <project> | milestone|task|meeting <ID> | <change> [| reason: ...]")
    target = parts[1].split()
    if len(target) != 2 or target[0].lower() not in ("milestone", "task", "meeting"):
        raise BadLine("the second part must be 'milestone <ID>', 'task <ID>' or 'meeting <ID>'")
    item = {"project": parts[0], "kind": target[0].lower(), "id": target[1], "reason": ""}
    allowed = MEETING_ACTIONS if item["kind"] == "meeting" else ACTIONS
    changes, agenda = {}, {}
    for part in parts[2:]:
        if part.lower().startswith("reason:"):
            item["reason"] = part[7:].strip()
            continue
        if part.lower().startswith(("items:", "none:")):
            key, _, value = part.partition(":")
            if not value.strip():
                raise BadLine(f"'{key.lower()}:' needs a value")
            agenda[key.lower()] = value.strip()
            continue
        word, _, rest = part.partition(" ")
        word = word.lower()
        if word not in allowed:
            raise BadLine(f"unknown change '{part}'; use one of: {', '.join(allowed)}, reason: ...")
        if word in ("done", "reopen") or (word == "cancel" and item["kind"] == "meeting" and not rest.strip()):
            if rest.strip():
                raise BadLine(f"'{word}' takes no value")
            changes[word] = True
        elif not rest.strip():
            raise BadLine(f"'{word}' needs a value")
        else:
            changes[word] = rest.strip()
    if not changes:
        raise BadLine("no change given")
    if item["kind"] == "milestone" and (set(changes) - {"due", "status", "confirmed", "title"}):
        raise BadLine("a milestone takes due, status, confirmed and title only")
    if item["kind"] == "task" and (set(changes) - {"done", "reopen", "due", "no-date", "title"}):
        raise BadLine("a task takes done, reopen, due, no-date or title only")
    if item["kind"] == "meeting":
        item["meeting"] = meeting_args(changes, item["id"], agenda)
    elif agenda:
        raise BadLine("items: and none: belong to a meeting's agenda line")
    item["changes"] = changes
    item["agenda"] = agenda
    return item


def resolve(name: str, covered: list[dict]) -> dict:
    exact = [e for e in covered if e["name"].lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    near = [e for e in covered if name.lower() in e["name"].lower()]
    if len(near) == 1:
        return near[0]
    if len(near) > 1:
        raise BadLine(f"'{name}' matches more than one project in this hub; use the full name")
    raise BadLine(f"no project named '{name}' in this hub's scope")


def needs_of(item: dict) -> tuple[list[str], list[str]]:
    """What the project's own scripts must contain to make this line: (tracker_edit.py marks, meetings_view.py
    marks). Older copies lack the meeting sub-command (core < 0.6.0) or renames and agendas (< 0.6.3)."""
    if item.get("kind") != "meeting":
        return (['add_argument("--title"'] if "title" in item.get("changes", {}) else []), []
    action = item.get("meeting", {}).get("action", "")
    newer = action in ("rename", "agenda")
    return ['add_parser("meeting"'] + ([f'add_parser("{action}"'] if newer else []), (["def agendas("] if newer else [])


def script_for(root: Path, item: dict | str = "") -> tuple[Path, bool]:
    """The project's own tracker_edit.py, or the hub's bundled copy when it has none or it is too old for this
    line. A meeting line also needs the own copy's sibling meetings_view.py."""
    item = item if isinstance(item, dict) else {"kind": item}
    own = root / ".familiar" / "scripts" / "tracker_edit.py"
    view = own.parent / "meetings_view.py"
    edit_marks, view_marks = needs_of(item)

    def has(path: Path, marks: list[str]) -> bool:
        text = path.read_text(encoding="utf-8", errors="replace")
        return all(m in text for m in marks)
    if own.is_file() and has(own, edit_marks) and (
            item.get("kind") != "meeting" or (view.is_file() and has(view, view_marks))):
        return own, False
    return fo.BUNDLED / "tracker_edit.py", True


def argv_for(item: dict, script: Path, root: Path, today: str | None) -> list[str]:
    if item["kind"] == "meeting":
        # Only move, cancel and edit-series are ever built here; add-series and add-oneoff are not reachable.
        args = [sys.executable, "-B", str(script), "meeting", item["meeting"]["action"], "--root", str(root),
                "--id", item["id"], "--json", "--via", "fleet hub", *item["meeting"]["flags"]]
    else:
        args = [sys.executable, "-B", str(script), item["kind"], "--root", str(root), "--id", item["id"],
                "--json", "--via", "fleet hub"]
        for key, value in item["changes"].items():
            args.append(f"--{key}")
            if value is not True:
                args.append(value)
    if item["reason"]:
        args += ["--reason", item["reason"]]
    if today:
        args += ["--today", today]
    return args


def run_json(argv: list[str]) -> dict:
    done = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(done.stdout)
    except ValueError:
        return {"ok": False, "error": (done.stderr or done.stdout or "no output").strip()[:300]}


def covered_projects(registry: Path) -> list[dict]:
    covered, _ = fo.in_scope(fo.read_registry(registry), fo.read_scope())
    return covered


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def describe(item: dict) -> str:
    what = "; ".join(k if v is True else f"{k} {v}" for k, v in item["changes"].items())
    what += "".join(f" | {k}: {v}" for k, v in (item.get("agenda") or {}).items())
    return f"{item['project']} | {item['kind']} {item['id']} | {what}" + (f" | reason: {item['reason']}" if item["reason"] else "")


SNOOZES_HEAD = ("# Agenda reminders (hub-local)\n\nOne line per \"Remind me on\" from the brief page, written by "
                "`scripts/apply_change.py apply` and never copied into a project. A line whose meeting has passed "
                "no longer counts.\n\n")
CHANGE_LOG_HEAD = ("# Hub change log\n\nOne line per change this hub applied, oldest first, written by "
                   "`scripts/apply_change.py apply`. Each project's own status-log has the full entry; the "
                   "`applied-*.json` records beside this file are the machine record.\n\n")


def snooze_key(item: dict) -> str:
    return f"{item['project']} | {item['id'].upper()} {item['meeting']['date']}"


def read_snoozes(out: Path) -> list[str]:
    """The snooze lines themselves (`<project> | <ID> <date> | remind <date>`), without the heading."""
    path = out / SNOOZES
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return [l for l in lines if l.count("|") == 2 and not l.startswith("#")]


def snooze_preview(item: dict, out: Path, today: str | None) -> dict:
    """A hub-local reminder, checked like a project change but never sent to the project: the meeting date must
    be one of that meeting's dates in the project's meetings.md (read only), and the reminder not in the past."""
    m = item["meeting"]
    if m["remind"] < (today or datetime.now().date().isoformat()):
        return {"ok": False, "error": f"{m['remind']} has already passed; pick a reminder date from today on"}
    path = Path(item["path"]) / "4-Trackers" / "meetings.md"
    if not path.is_file():
        return {"ok": False, "error": "4-Trackers/meetings.md does not exist in that project"}
    sys.path.insert(0, str(fo.BUNDLED))
    try:
        import meetings_view as mv
    finally:
        sys.path.remove(str(fo.BUNDLED))
    found = mv.expand(path.read_text(encoding="utf-8"), datetime.strptime(m["date"], "%Y-%m-%d").date(), 0)
    if not [o for o in found["occurrences"]
            if o["date"] == m["date"] and (o["series"] or o["id"]).lower() == item["id"].lower()]:
        return {"ok": False, "error": f"{item['id']} has no meeting on {m['date']} "
                                      "(not one of its dates, or moved or cancelled)"}
    key = snooze_key(item)
    was = next((l for l in read_snoozes(out) if l.startswith(key + " |")), "")
    after = f"{key} | remind {m['remind']}"
    if was == after:
        return {"ok": False, "error": f"a reminder on {m['remind']} is already set for that meeting; nothing to change"}
    return {"ok": True, "hub_local": True, "file": f"_overview/{SNOOZES}", "before": was or "no reminder set",
            "after": after, "log": "none (hub-local: nothing is written to the project)",
            "changes": [f"agenda reminder for {m['date']}: "
                        + (was.rsplit("| ", 1)[-1] if was else "none") + f" -> remind {m['remind']}"]}


def apply_snooze(item: dict, out: Path) -> dict:
    """Write one reminder into _overview/agenda-snoozes.md, replacing any earlier one for that meeting date."""
    key = snooze_key(item)
    lines = [l for l in read_snoozes(out) if not l.startswith(key + " |")] + [item["result"]["after"]]
    path = out / SNOOZES
    temp = path.with_name(SNOOZES + ".tmp")
    temp.write_text(SNOOZES_HEAD + "\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(path)
    return {**item["result"], "dry_run": False}


def log_changes(out: Path, done: list[dict], failed: list[dict]) -> Path:
    """One readable line per applied or failed change, appended to _overview/change-log.md."""
    path = out / CHANGE_LOG
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for item, ok in [(i, True) for i in done] + [(i, False) for i in failed]:
        r = item["result"]
        if ok:
            what = "; ".join(r.get("changes") or []) or describe(item).split(" | ", 2)[-1]
            result = ("hub-local" if r.get("hub_local") else
                      "applied with the hub's bundled tracker_edit.py" if item.get("bundled") else "applied")
        else:
            what = describe(item).split(" | ", 2)[-1]
            result = "failed: " + ((r.get("error") or "no reason given").splitlines() or [""])[0][:160]
        cells = [when, item["project"], f"{item['kind']} {item['id']}", what]
        cells += ([f"reason: {item['reason']}"] if item.get("reason") and ok else []) + [result]
        rows.append("- " + " | ".join(" ".join(str(c).replace("|", "/").split()) for c in cells))
    if rows:
        fresh = not path.is_file() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8") as f:
            f.write((CHANGE_LOG_HEAD if fresh else "") + "\n".join(rows) + "\n")
    return path


def read_time_off(path) -> list[dict]:
    """The user's time-off periods (`~/.familiar/time-off.md`), read for a warning only; unreadable is none."""
    try:
        import brief
        return brief.read_time_off(Path(path) if path else brief.TIME_OFF)[0]
    except Exception:  # a warning is a courtesy: never let it stop a preview
        return []


def time_off_note(item: dict, periods: list[dict]) -> str:
    lands = item.get("meeting", {}).get("lands", "")
    if not lands:
        return ""
    import brief
    away = brief.off_on(periods, lands)
    return f"{lands} is in your time off ({brief.span(away)})" if away else ""


def meeting_notes(r: dict, item: dict) -> None:
    """What a meeting change adds to the usual output: a new overlap, a time-off day, the calendar reminder."""
    if item["kind"] != "meeting":
        return
    for clash in r.get("conflicts") or []:
        print(f"         NEW OVERLAP: {clash}")
    if item.get("time_off"):
        print(f"         NOTE: {item['time_off']}")
    if r.get("calendar_note"):
        print(f"         {r['calendar_note']}")


def preview(args) -> int:
    out = Path(args.out)
    pending = Path(args.pending) if args.pending else out / PENDING
    if not pending.is_file():
        print(f"ERROR: {pending} does not exist. Write the change lines there first (see --help).", file=sys.stderr)
        return 1
    text = pending.read_text(encoding="utf-8")
    covered = covered_projects(Path(args.registry))
    time_off = read_time_off(getattr(args, "time_off", None))
    items, problems, seen = [], [], set()
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.strip().startswith("#") or raw.lower().startswith("apply these changes"):
            continue
        try:
            item = parse_line(raw)
            entry = resolve(item["project"], covered)
            key = (entry["name"], item["kind"], item["id"].lower(), item.get("meeting", {}).get("on", ""))
            if key in seen:
                raise BadLine(f"{item['kind']} {item['id']} on {entry['name']} appears twice in this batch; combine the changes into one line")
            seen.add(key)
        except BadLine as err:
            problems.append(f"line {number}: {err}")
            continue
        root = Path(entry["path"])
        local = item.get("meeting", {}).get("hub_local", False)
        script, bundled = (Path(), False) if local else script_for(root, item)
        item.update({"line": number, "project": entry["name"], "path": str(root), "bundled": bundled,
                     "lifecycle": entry.get("lifecycle") or "active"})
        item["result"] = snooze_preview(item, out, args.today) if local else \
            run_json(argv_for(item, script, root, args.today) + ["--dry-run"])
        item["time_off"] = time_off_note(item, time_off)
        item["argv_script"] = str(script)
        items.append(item)
    good = [i for i in items if i["result"].get("ok")]
    out.mkdir(parents=True, exist_ok=True)
    (out / PREVIEW).write_text(json.dumps({"created": datetime.now().isoformat(timespec="seconds"),
                                           "pending_sha256": digest(text), "pending": str(pending),
                                           "today": args.today, "items": good}, indent=2), encoding="utf-8")
    print(f"Batch preview: {len(good)} change(s) ready, {len(items) - len(good)} refused, {len(problems)} unreadable line(s). Nothing has been written to any project.\n")
    for item in items:
        r = item["result"]
        tags = (" [project's lifecycle: " + item["lifecycle"] + "]" if item["lifecycle"] != "active" else "") + \
               (" [uses the hub's bundled tracker_edit.py; this project is behind on core]" if item["bundled"] else "")
        if not r.get("ok"):
            print(f"REFUSED  {describe(item)}\n         {r.get('error')}\n")
            continue
        if r.get("hub_local"):
            print(f"OK       {item['project']} — {r['file']} [hub-local: a reminder for you, never written to the project]")
        else:
            print(f"OK       {item['project']} — {r['file']} (revision {r['revision_before']} -> {r['revision_after']}){tags}")
        for label, text in (("before", r["before"]), ("after", r["after"])):
            lines = text.splitlines() or [""]
            print(f"         {label + ':':<7} {lines[0]}")
            for more in lines[1:]:  # a wrapped task's continuation lines
                print(f"                 {more.strip()}")
        print(f"         log:    {r['log']}")
        meeting_notes(r, item)
        if r.get("stamp_added"):
            print("         note:   this file has no revision stamp yet; the change adds its schema header lines")
        for key in ("dependents", "unblocked_or_dependent"):
            for d in r.get(key) or []:
                print(f"         still depends on this: {d}")
        if r.get("closes_with_open_dependents"):
            print("         NOTE: this closes a milestone that open milestones depend on")
        shown = r.get("unpropagated") or []
        for u in shown[:4]:
            print(f"         still names it: {u[:140]}")
        if len(shown) > 4:
            print(f"         ... and {len(shown) - 4} more place(s)")
        print()
    for p in problems:
        print("UNREADABLE  " + p)
    if problems or len(good) != len(items):
        print("\nFix or drop the refused lines in the pending file and preview again, or apply the OK lines only.")
    return 0 if good or not (items or problems) else 1


def apply(args) -> int:
    out = Path(args.out)
    if not args.yes:
        print("REFUSED: apply needs --yes, and only after the user has seen the preview and said yes.", file=sys.stderr)
        return 1
    path = out / PREVIEW
    if not path.is_file():
        print("REFUSED: no preview to apply. Run `python scripts/apply_change.py preview` first.", file=sys.stderr)
        return 1
    saved = json.loads(path.read_text(encoding="utf-8"))
    pending = Path(saved["pending"])
    if not pending.is_file() or digest(pending.read_text(encoding="utf-8")) != saved["pending_sha256"]:
        print("REFUSED: the pending file changed since the preview. Preview again.", file=sys.stderr)
        return 1
    done, failed, bumped = [], [], {}
    for item in saved["items"]:
        if item["result"].get("hub_local"):
            # Checked again: the meeting may have moved or been cancelled in the project since the preview.
            again = snooze_preview(item, out, saved.get("today"))
            if again.get("ok") and again["after"] == item["result"]["after"]:
                done.append({**item, "result": apply_snooze(item, out)})
            else:
                failed.append({**item, "result": again if not again.get("ok") else
                               {"ok": False, "error": "the reminder changed since the preview. Preview again."}})
            continue
        script = Path(item["argv_script"])
        root = Path(item["path"])
        file_key = (item["path"], item["result"]["file"])
        # Earlier lines in this batch already bumped the same tracker file's revision.
        expect = item["result"]["revision_before"] + bumped.get(file_key, 0)
        result = run_json(argv_for(item, script, root, saved.get("today")) + ["--expect-revision", str(expect)])
        if result.get("ok"):
            bumped[file_key] = bumped.get(file_key, 0) + 1
            done.append({**item, "result": result})
        else:
            failed.append({**item, "result": result})
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    (out / f"applied-{stamp}.json").write_text(json.dumps({"applied": done, "failed": failed}, indent=2), encoding="utf-8")
    path.unlink()
    log = log_changes(out, done, failed)
    print(f"Applied {len(done)} change(s); {len(failed)} not applied. Record: {out / f'applied-{stamp}.json'}; "
          f"change log: {log}\n")
    for item in done:
        r = item["result"]
        print(f"DONE     {describe(item)}")
        if r.get("hub_local"):
            print(f"         {r['file']}: {r['after']}")
            continue
        print(f"         {r['file']} now at revision {r['revision_after']}; log written: {r.get('log_written')}; views: {r.get('views', 'n/a')}")
        meeting_notes(r, item)
        if r.get("warning"):
            print("         WARNING: " + r["warning"])
        for u in (r.get("unpropagated") or [])[:6]:
            print(f"         still names the old value: {u[:140]}")
    for item in failed:
        print(f"FAILED   {describe(item)}\n         {item['result'].get('error')}")
    if [i for i in done if not i["result"].get("hub_local")]:
        print("\nEach project's status-log records the change and that its snapshot, other tracker and "
              "downstream milestones are not yet reconciled. The next session in that project does that; "
              "this hub does not edit those files.")
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("preview", "apply"))
    ap.add_argument("--yes", action="store_true", help="apply only: the user has seen the preview and agreed")
    ap.add_argument("--registry", default=str(fo.DEFAULT_REGISTRY))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--pending", help=f"default: <out>/{PENDING}")
    ap.add_argument("--today", help="YYYY-MM-DD, for testing")
    ap.add_argument("--time-off", help="the user's time-off file (default: beside the registry)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    return preview(args) if args.command == "preview" else apply(args)


if __name__ == "__main__":
    sys.exit(main())
