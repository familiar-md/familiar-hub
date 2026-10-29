#!/usr/bin/env python3
"""fleet_overview.py — one digest across every registered familiar project. Read-only on projects.

    python scripts/fleet_overview.py                      # write _overview/*.md from ~/.familiar/fleet.md
    python scripts/fleet_overview.py --stdout             # print the digest, write nothing
    python scripts/fleet_overview.py --json               # machine-readable, write nothing
    python scripts/fleet_overview.py --discover <dir>     # list unregistered projects under <dir>
    python scripts/fleet_overview.py --discover <dir> --apply   # ...and append them, creating the registry if needed
    python scripts/fleet_overview.py --set-lifecycle NAME paused   # active | paused | reference | closed
    python scripts/fleet_overview.py --set-included NAME no        # stop reading a project; its row stays
    python scripts/fleet_overview.py --set-category NAME work      # work | personal | unset

The registry is a Markdown table (`name | path | category | included | added | lifecycle`) in
`~/.familiar/fleet.md`. For each included project this runs the project's OWN
`.familiar/scripts/{project_check,task_frontier,timeline_views,meetings_view}.py` with `--root --json`, so the
answer matches that project's tree; if a project's copy is missing or too old for `--json`, it
falls back to the copy bundled beside this script (`scripts/bundled/`) and says so. Status and the
last-updated date are read from the project's status snapshot. Lifecycle belongs to the hub: it is
the registry's `lifecycle` column, and a blank one means `active`. An active project whose snapshot
was last updated more than `stale_days` ago (`hub-settings.md`, default 30) is marked STALE, and one
with no recorded date is marked undated. Other missing fields are reported as missing.

`hub-settings.md` in the hub may set `categories: work` (or `personal`, a list, or `all`): a
scoped hub reads only registry rows of those categories, so one hub per category keeps work and
personal apart.

The only files written are `_overview/*.md` inside the hub, and the registry (`--discover --apply`
creates it if missing and appends rows; `--set-lifecycle`, `--set-included` and `--set-category` each edit one cell of one row, after a dated
backup). This is the only place the registry
is created. Nothing is ever written into a project. Standard library only.
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
BUNDLED = Path(__file__).resolve().parent / "bundled"
DEFAULT_REGISTRY = Path.home() / ".familiar" / "fleet.md"
HUB_SETTINGS = HUB / "hub-settings.md"
LIFECYCLE = re.compile(r"\*\*Lifecycle:\*\*\s*(active|paused|reference|closed)\b", re.IGNORECASE)
AS_OF = re.compile(r"_As of:\s*(\d{4}-\d{2}-\d{2})_")
STATUS = re.compile(r"\*\*Status:\s*([^*]+?)\*\*(?:\s*—\s*confirmed\s*(\d{4}-\d{2}-\d{2}))?")
LAST_UPDATED = re.compile(r"<!--\s*last-updated:\s*(\d{4}-\d{2}-\d{2})\s*-->")
REVISION = re.compile(r"<!--\s*revision:\s*(\d+)\s*-->")
NEXT_MS = re.compile(r"^\s*-\s*\*\*Next milestone\(s\):\*\*\s*(.+)$", re.MULTILINE)
LIFECYCLES = ["active", "paused", "reference", "closed"]
ORDER = LIFECYCLES
STALE = re.compile(r"^\s*[-*]?\s*`?stale_days`?\s*:\s*`?(\d+)`?\s*$", re.MULTILINE)
DEFAULT_STALE_DAYS = 30
# The fleet hub is the only thing that creates or edits the registry; a project never writes it.
REGISTRY_HEADER = """# familiar fleet registry

The familiar projects on this machine, read by the fleet hub. One row per project; `included` = `no`
keeps a row as a record without scanning it. `category` is matched by each hub's `hub-settings.md`.
Created by the hub's `scripts/fleet_overview.py --discover <dir> --apply` on {today}. The hub owns
every cell: add projects with `--discover --apply`, and change a row with `--set-included`,
`--set-category` or `--set-lifecycle` (active, paused, reference or closed; blank means active), or by hand.

| name | path | category | included | added | lifecycle |
|---|---|---|---|---|---|
"""


# ---------------------------------------------------------------- registry

def read_registry(path: Path) -> list[dict]:
    """Rows of the first table whose header has name and path."""
    rows, header = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            if header and rows:
                break
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            lowered = [c.lower() for c in cells]
            if "name" in lowered and "path" in lowered:
                header = lowered
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        row["path"] = row.get("path", "").strip("`")
        row["included"] = row.get("included", "yes").lower() not in ("no", "false", "n")
        row["lifecycle"] = row.get("lifecycle", "").strip("` ").lower()
        rows.append(row)
    return rows


SCOPE = re.compile(r"^\s*[-*]?\s*`?categories`?\s*:\s*`?([^`\n]+?)`?\s*$", re.MULTILINE)
UNSET = {"", "unset", "none", "-", "—"}


def read_scope(path: Path = None) -> set[str] | None:
    """The categories this hub covers, from `hub-settings.md`; None means every category."""
    m = SCOPE.search(read(path or HUB_SETTINGS))
    cats = {c.strip().lower() for c in m.group(1).split(",")} if m else {"all"}
    return None if "all" in cats or not cats - {""} else cats - {""}


def in_scope(entries: list[dict], scope: set[str] | None) -> tuple[list[dict], list[str]]:
    """(included rows this hub covers, names of included rows with no category). A scoped hub never
    reads a project outside its categories; an uncategorized one is named so it can be fixed."""
    included = [e for e in entries if e["included"]]
    if scope is None:
        return included, []
    cat = lambda e: e.get("category", "").strip().lower()
    return ([e for e in included if cat(e) in scope],
            [e["name"] for e in included if cat(e) in UNSET])


def read_stale_days(path: Path = None) -> int:
    m = STALE.search(read(path or HUB_SETTINGS))
    return int(m.group(1)) if m and int(m.group(1)) > 0 else DEFAULT_STALE_DAYS


CELL_VALUES = {"lifecycle": LIFECYCLES, "included": ["yes", "no"], "category": ["work", "personal", "unset"]}


def set_lifecycle(registry: Path, name: str, value: str, scope: set[str] | None, today: str) -> str:
    return set_cell(registry, name, "lifecycle", value, scope, today)


def set_cell(registry: Path, name: str, column: str, value: str, scope: set[str] | None, today: str) -> str:
    """Set one row's lifecycle, included or category cell in the registry, adding the lifecycle column
    if the table has none. Refuses a name outside this hub's scope, an unknown or ambiguous name, and an
    unrecognised value. Keeps a dated backup of the registry. Every line that is not a table row is left
    as it was."""
    value = value.strip().lower()
    if value not in CELL_VALUES[column]:
        raise SystemExit(f"ERROR: {column} must be one of {', '.join(CELL_VALUES[column])}, not {value!r}.")
    lines = registry.read_text(encoding="utf-8").splitlines()
    split = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    head = next((i for i, l in enumerate(lines) if l.strip().startswith("|")
                 and {"name", "path"} <= {c.lower() for c in split(l)}), None)
    if head is None:
        raise SystemExit(f"ERROR: no registry table found in {registry}.")
    end = head + 1
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    header = [c.lower() for c in split(lines[head])]
    rows = [(i, split(lines[i])) for i in range(head + 2, end)]
    matches = [(i, c) for i, c in rows if c[header.index("name")].lower() == name.strip().lower()]
    if len(matches) != 1:
        raise SystemExit(f"ERROR: {'no registry row' if not matches else 'more than one row'} named {name!r}.")
    target = matches[0][0]
    cat_i = header.index("category") if "category" in header else None
    cells = matches[0][1]
    cat = cells[cat_i].lower() if cat_i is not None and cat_i < len(cells) else ""
    if scope is not None and cat not in scope:
        raise SystemExit(f"ERROR: {name!r} is not in this hub's scope ({', '.join(sorted(scope))}).")
    backup = registry.with_name(registry.name + f".bak-{today}")
    if not backup.exists():
        backup.write_bytes(registry.read_bytes())
    if "lifecycle" not in header:
        header.append("lifecycle")
        lines[head] = "| " + " | ".join(split(lines[head]) + ["lifecycle"]) + " |"
        lines[head + 1] = lines[head + 1].rstrip() + "---|"
        for i, c in rows:
            lines[i] = "| " + " | ".join(c + [""]) + " |"
    if column not in header:
        raise SystemExit(f"ERROR: the registry table has no {column!r} column.")
    li = header.index(column)
    cells = split(lines[target])
    cells += [""] * (len(header) - len(cells))
    old = cells[li] or {"lifecycle": "active", "included": "yes"}.get(column, "unset")
    cells[li] = value
    lines[target] = "| " + " | ".join(cells) + " |"
    registry.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return f"{name}: {column} {old} -> {value} (backup {backup.name})"


def scope_note(scope: set[str] | None, uncategorized: list[str]) -> str:
    if scope is None:
        return "Scope: every category in the registry."
    note = (f"Scope: {', '.join(sorted(scope))} only (`hub-settings.md`). Projects in other categories "
            "are not read.")
    if uncategorized:
        note += (f" Not shown because their registry row has no category: {', '.join(uncategorized)}; "
                 "set `category` in `~/.familiar/fleet.md`.")
    return note


def norm(p: str) -> str:
    # Windows and default macOS volumes are case-insensitive; Linux is not, where lowering could
    # merge two different folders.
    s = str(Path(p)).rstrip("\\/")
    return s.lower() if sys.platform in ("win32", "darwin") else s


def discover(base: Path, registered: set[str]) -> list[Path]:
    found = []
    for marker in sorted(base.glob("**/.familiar-project")):
        folder = marker.parent
        parts = folder.relative_to(base).parts
        if parts and len(parts) <= 3 and not any(x.startswith("_") for x in parts)                 and norm(str(folder)) not in registered:
            found.append(folder)
    return found


# ---------------------------------------------------------------- per-project reads

def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# Health findings in these areas name files from a project's inputs, drafts or knowledge. The hub
# never reads those folders, so it reports how many issues there are, not which files.
PRIVATE_AREAS = {"knowledge", "drafts", "inputs", "hygiene"}


def private_counts(findings: list[dict]) -> list[dict]:
    kept = [f for f in findings if f.get("area") not in PRIVATE_AREAS]
    counts: dict[str, int] = {}
    for f in findings:
        if f.get("area") in PRIVATE_AREAS:
            counts[f["area"]] = counts.get(f["area"], 0) + 1
    for area, n in sorted(counts.items()):
        kept.append({"area": area, "level": "attention",
                     "message": f"{n} {area} issue(s); run /project-status check-in in that project for details."})
    return kept


def run_script(root: Path, name: str, today: str, extra: list[str] | None = None, current=None):
    """(data, source, error). The project's copy first, the bundled copy as the fallback. With `extra`
    flags, a project copy too old to know them falls back with source `bundled-newer-flag`. With `current`
    (a test on the parsed output), a project copy whose output lacks a newer field falls back with source
    `bundled-newer-output`."""
    attempts = []
    project_copy = root / ".familiar" / "scripts" / name
    if project_copy.is_file():
        attempts.append(("project", project_copy))
    attempts.append(("bundled", BUNDLED / name))
    last_err, too_old = "script not found", False
    for source, script in attempts:
        try:
            cmd = [sys.executable, str(script), "--root", str(root), "--json"]
            if name != "task_frontier.py":  # the frontier does not depend on today's date
                cmd += ["--today", today]
            cmd += extra or []
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = f"{name}: {exc}"
            continue
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
                if source == "project" and current and not current(data):
                    too_old, last_err = "output", f"{name}: project copy predates a field the hub reads"
                    continue
                return data, ("bundled-newer-output" if too_old == "output" else
                              "bundled-newer-flag" if too_old else source), None
            except json.JSONDecodeError:
                last_err = f"{name}: output was not JSON"
                continue
        message = (proc.stderr or proc.stdout).strip().splitlines()
        last_err = message[-1] if message else f"{name} exited {proc.returncode}"
        if source == "project" and extra and "unrecognized arguments" in last_err:
            too_old = True
        # A missing tracker file is a fact about the project, not a script fault: do not retry.
        if "does not exist" in last_err:
            break
    return None, None, last_err


def inspect(entry: dict, today: str, window: int = 14, stale_days: int = DEFAULT_STALE_DAYS) -> dict:
    """`window` widens the timeline's "upcoming" list past its 14-day default (the brief's lookahead).
    Lifecycle is the registry's; blank is active. An active project is marked `stale` (snapshot older
    than `stale_days`) or `undated` (snapshot has no last-updated date)."""
    root = Path(entry["path"])
    life = entry.get("lifecycle") or "active"
    out = {"name": entry["name"], "path": entry["path"], "category": entry.get("category", ""),
           "flags": [], "lifecycle": life if life in LIFECYCLES else "active", "revisions": {}}
    if life not in LIFECYCLES:
        out["flags"].append(f"registry lifecycle {life!r} is not one of {', '.join(LIFECYCLES)}; treated as active")
    if not (root / ".familiar-project").is_file():
        out["state"] = "missing" if not root.exists() else "not-a-project"
        out["flags"].append("folder not found on disk" if not root.exists() else "no .familiar-project marker")
        return out
    out["state"] = "ok"
    try:
        installed = json.loads(read(root / ".familiar" / "installed.json") or "{}")
        out["version"] = installed.get("core", {}).get("version")
    except json.JSONDecodeError:
        out["version"] = None
    if not out.get("version"):
        out["flags"].append("no readable .familiar/installed.json version")
    ext_dir = root / ".familiar" / "extensions"
    out["extensions"] = sorted(p.name for p in ext_dir.iterdir() if p.is_dir()) if ext_dir.is_dir() else []

    snap = read(root / "5-System" / "Memory" / "status-snapshot.md")
    if not snap:
        out["flags"].append("status snapshot missing")
    else:
        theirs = LIFECYCLE.search(snap)
        if theirs and theirs.group(1).lower() != out["lifecycle"]:
            out["flags"].append(f"the project's snapshot says Lifecycle {theirs.group(1).lower()}; the hub's "
                                f"registry says {out['lifecycle']} and wins (change it with --set-lifecycle)")
        m = LAST_UPDATED.search(snap)
        out["last_updated"] = m.group(1) if m else None
        if not m:
            out["flags"].append("snapshot has no last-updated date")
        m = AS_OF.search(snap)
        out["as_of"] = m.group(1) if m else None
        m = STATUS.search(snap)
        out["status"] = m.group(1).strip() if m else None
        out["confirmed"] = m.group(2) if m else None
        m = NEXT_MS.search(snap)
        out["next_milestones"] = m.group(1).strip() if m else None
        rev = REVISION.search(snap)
        out["revisions"]["snapshot"] = rev.group(1) if rev else "?"
    for label_, rel in (("tasks", "4-Trackers/tasks.md"), ("timeline", "4-Trackers/timeline.md")):
        if (root / rel).is_file():
            rev = REVISION.search(read(root / rel))
            out["revisions"][label_] = rev.group(1) if rev else "?"

    out["sources"] = {}
    data, src, err = run_script(root, "project_check.py", today)
    if data is None:
        out["flags"].append(f"project_check failed: {err}")
    else:
        out["sources"]["check"] = src
        out["check"] = private_counts([f for f in data.get("findings", []) if f.get("level") == "attention"])
    data, src, err = run_script(root, "task_frontier.py", today)
    if data is None:
        out["flags"].append(f"task_frontier failed: {err}")
    else:
        out["sources"]["tasks"] = src
        out["frontier"], out["blocked"] = data.get("frontier", []), data.get("blocked", [])
    data, src, err = run_script(root, "timeline_views.py", today,
                                ["--window", str(window)] if window != 14 else None)
    if data is None:
        out["flags"].append(f"timeline_views failed: {err}")
    else:
        out["sources"]["timeline"] = src
        out["timeline"] = data
    # Meetings are optional: a project without 4-Trackers/meetings.md reports `present: false` and nothing is
    # flagged. The bundled script is the normal answer for a project that has not been carried yet, so its
    # source is not reported as a problem.
    # Agendas (core 0.6.3): an older project copy reports occurrences without `agenda`, so an agenda set from
    # the hub would read as missing; the bundled copy reads the same meetings.md with it.
    data, src, err = run_script(root, "meetings_view.py", today, ["--window", str(window)],
                                current=lambda d: all("agenda" in o for o in d.get("occurrences", [])))
    if data is None:
        out["flags"].append(f"meetings_view failed: {err}")
    elif data.get("present"):
        out["meetings"] = data
        out["flags"] += [f"meetings.md: {problem}" for problem in data.get("problems", [])]
    if out["lifecycle"] == "active":
        try:
            days = (date.fromisoformat(today) - date.fromisoformat(out.get("last_updated") or "")).days
        except ValueError:
            out["stale"] = "undated"
        else:
            out["age_days"] = days
            if days > stale_days:
                out["stale"] = "stale"
    for part, source in out["sources"].items():
        if source == "bundled":
            out["flags"].append(f"{part}: used the hub's bundled script (project copy missing or too old)")
        elif source == "bundled-newer-flag":
            out["flags"].append(f"{part}: project copy predates --window; used the hub's bundled script")
    return out


# ---------------------------------------------------------------- rendering

def stamp(projects: list[dict], today: str) -> str:
    parts = [p["name"] + " " + ", ".join(f"{k} r{v}" for k, v in p["revisions"].items())
             for p in projects if p.get("revisions")]
    return f"<!-- generated by scripts/fleet_overview.py on {today}; do not edit. Sources: " + "; ".join(parts) + " -->"


def label(p: dict) -> str:
    """The lifecycle, with STALE (old snapshot) or STALE? (no date) appended for an active project."""
    return p["lifecycle"] + {"stale": " — STALE", "undated": " — STALE?"}.get(p.get("stale"), "")


def stale_text(p: dict) -> str:
    if p.get("stale") == "stale":
        return f"snapshot {p['age_days']} days old ({p['last_updated']}); treat as unconfirmed"
    return "snapshot has no last-updated date, so its age is unknown"


def stale_lines(projects) -> list[str]:
    return [f"- **{p['name']}** — {stale_text(p)}" for p in projects if p.get("stale")]


def digest(projects, today, note="") -> str:
    out = [stamp(projects, today), f"# Fleet digest — {today}", "", *([note, ""] if note else []),
           f"{len(projects)} registered project(s) included. Lifecycle is set in the hub's registry "
           "(`--set-lifecycle`); a project with none is active.", ""]
    flagged = stale_lines(projects)
    if flagged:
        out += ["## Stale or undated (active projects)", "", *flagged, ""]
    for state in ORDER:
        group = [p for p in projects if p.get("state") == "ok" and p["lifecycle"] == state]
        if not group:
            continue
        out += [f"## {state.capitalize()} ({len(group)})", ""]
        for p in group:
            line = f"- **{p['name']}** — {p.get('status') or 'status not recorded'}"
            if p.get("confirmed"):
                line += f" (confirmed {p['confirmed']})"
            line += f" — v{p.get('version') or '?'}"
            if p["extensions"]:
                line += f" + {', '.join(p['extensions'])}"
            line += f" — last touched {p.get('last_updated') or 'not recorded'}"
            if p.get("stale"):
                line += " — **" + stale_text(p) + "**"
            out.append(line)
            tl = p.get("timeline") or {}
            bits = []
            if tl.get("overdue"):
                bits.append(f"{len(tl['overdue'])} overdue milestone(s): "
                            + "; ".join(f"{m['name']} ({m['due']})" for m in tl["overdue"]))
            if tl.get("upcoming"):
                bits.append(f"{len(tl['upcoming'])} due in 14 days")
            if "frontier" in p:
                bits.append(f"{len(p['frontier'])} startable task(s), {len(p['blocked'])} blocked")
            if p.get("check"):
                bits.append(f"{len(p['check'])} check-in attention item(s)")
            if bits:
                out.append("  - " + " · ".join(bits))
            if p.get("next_milestones"):
                out.append(f"  - Next: {p['next_milestones']}")
            if p["flags"]:
                out.append(f"  - Missing/degraded: {'; '.join(p['flags'])}")
        out.append("")
    bad = [p for p in projects if p.get("state") != "ok"]
    if bad:
        out += [f"## Not readable ({len(bad)})", ""]
        out += [f"- **{p['name']}** — {p['path']} — {'; '.join(p['flags'])}" for p in bad] + [""]
    return "\n".join(out)


def frontier_md(projects, today) -> str:
    out = [stamp(projects, today), f"# Startable tasks across the fleet — {today}", "",
           "Every open task whose blockers are all cleared, per each project's own `tasks.md`.", ""]
    for p in projects:
        if p.get("state") != "ok" or "frontier" not in p:
            continue
        out += [f"## {p['name']} ({label(p)}) — {len(p['frontier'])} startable, {len(p['blocked'])} blocked", ""]
        lines = []
        for t in p["frontier"]:
            ident = f"{t['id']} " if t.get("id") else ""
            track = f" [{t['track']}]" if t.get("track") else ""
            lines.append(f"- {ident}{t['title']}{track}")
        out += lines or ["None."]
        out.append("")
    return "\n".join(out)


def dated_rows(projects) -> list[tuple]:
    """(due, kind, project, milestone) for every overdue and upcoming milestone, by date."""
    rows = []
    for p in projects:
        tl = p.get("timeline") or {}
        for kind in ("overdue", "upcoming"):
            for m in tl.get(kind, []):
                rows.append((m["due"], kind, p, m))
    return sorted(rows, key=lambda r: r[0])


def crowded_weeks(rows, minimum=3) -> list[str]:
    by_week = {}
    for due, kind, p, m in rows:
        iso = date.fromisoformat(due).isocalendar()
        by_week.setdefault((iso[0], iso[1]), set()).add(p["name"])
    return [f"ISO week {y}-W{w:02d}: {', '.join(sorted(names))}"
            for (y, w), names in sorted(by_week.items()) if len(names) >= minimum]


def next_two_weeks_md(projects, today) -> str:
    out = [stamp(projects, today), f"# Next two weeks across the fleet — {today}", ""]
    rows = dated_rows(projects)
    out += [f"- {due} — **{p['name']}** ({label(p)}) — {m['name']}"
            + (" — OVERDUE" if kind == "overdue" else "") + f" — {m['status'] or 'status not set'}"
            for due, kind, p, m in rows] or ["None."]
    crowded = crowded_weeks(rows)
    out += ["", "## Crowded weeks (3+ projects with a date)", ""] + ([f"- {c}" for c in crowded] or ["None."])
    skipped = [p["name"] for p in projects if p.get("state") == "ok" and "timeline" not in p]
    if skipped:
        out += ["", "## No readable timeline", "", ", ".join(skipped)]
    return "\n".join(out) + "\n"


def health_md(projects, today, note="") -> str:
    out = [stamp(projects, today), f"# Fleet health — {today}", "", *([note, ""] if note else [])]
    for p in projects:
        items = list(p["flags"]) + [c["message"] for c in p.get("check", [])]
        if p.get("state") == "ok" and p["lifecycle"] != "active":
            items = [f"(lifecycle {label(p)}: attention items are informational)"] + items
        elif p.get("stale"):
            items = [label(p)] + items
        out += [f"## {p['name']}", ""] + ([f"- {i}" for i in items] or ["Nothing flagged."]) + [""]
    return "\n".join(out)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--out", default=str(HUB / "_overview"))
    ap.add_argument("--today", help="YYYY-MM-DD, for testing")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--discover", metavar="DIR")
    ap.add_argument("--apply", action="store_true", help="with --discover: append found projects to the registry")
    ap.add_argument("--set-lifecycle", nargs=2, metavar=("NAME", "VALUE"),
                    help="set a registry row's lifecycle: " + " | ".join(LIFECYCLES))
    ap.add_argument("--set-included", nargs=2, metavar=("NAME", "VALUE"),
                    help="include or exclude a registry row: yes | no (the row is kept either way)")
    ap.add_argument("--set-category", nargs=2, metavar=("NAME", "VALUE"),
                    help="set a registry row's category: " + " | ".join(CELL_VALUES["category"]))
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    today = args.today or date.today().isoformat()
    registry = Path(args.registry)
    scope = read_scope()
    if not registry.is_file() and not args.discover:
        raise SystemExit(f"ERROR: registry {registry} not found. Create it from the hub with "
                         "`python scripts/fleet_overview.py --discover <folder holding your projects> --apply`.")
    for column, pair in (("lifecycle", args.set_lifecycle), ("included", args.set_included),
                         ("category", args.set_category)):
        if pair:
            if not registry.is_file():
                raise SystemExit(f"ERROR: registry {registry} not found.")
            print(set_cell(registry, pair[0], column, pair[1], scope, today))
            return
    entries = read_registry(registry) if registry.is_file() else []

    if args.discover:
        found = discover(Path(args.discover), {norm(e["path"]) for e in entries})
        # A `|` would split the table cell; such a folder has to be renamed or added by hand.
        unstorable = [f for f in found if "|" in str(f)]
        found = [f for f in found if f not in unstorable]
        for f in found:
            print(f"unregistered: {f}")
        for f in unstorable:
            print(f"skipped (path contains `|`, which the table cannot hold): {f}")
        if not found:
            print("no unregistered projects found")
        if args.apply and found:
            # A hub scoped to one category files what it finds under that category.
            category = next(iter(scope)) if scope and len(scope) == 1 else "unset"
            if not registry.is_file():
                registry.parent.mkdir(parents=True, exist_ok=True)
                registry.write_text(REGISTRY_HEADER.format(today=today), encoding="utf-8", newline="\n")
                print(f"created {registry}")
            with registry.open("a", encoding="utf-8", newline="\n") as fh:
                # A table written before the lifecycle column existed gets rows without one (blank = active).
                first = next((l for l in read(registry).splitlines() if l.strip().startswith("|")), "")
                tail = " active |" if "lifecycle" in first.lower() else ""
                for f in found:
                    fh.write(f"| {f.name} | {f} | {category} | yes | {today} |{tail}\n")
            print(f"appended {len(found)} row(s) to {registry}")
        elif found and not registry.is_file():
            print(f"{registry} does not exist yet; add --apply to create it with these projects.")
        return

    covered, uncategorized = in_scope(entries, scope)
    note = scope_note(scope, uncategorized)
    stale_days = read_stale_days()
    projects = [inspect(e, today, stale_days=stale_days) for e in covered]
    if args.json:
        print(json.dumps({"today": today, "scope": sorted(scope) if scope else "all",
                          "uncategorized": uncategorized, "projects": projects}, indent=2, default=str))
        return
    files = {"digest.md": digest(projects, today, note), "frontier.md": frontier_md(projects, today),
             "next-two-weeks.md": next_two_weeks_md(projects, today), "health.md": health_md(projects, today, note)}
    if args.stdout:
        print(files["digest.md"])
        return
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (out / name).write_text(body, encoding="utf-8", newline="\n")
    ok = sum(1 for p in projects if p.get("state") == "ok")
    print(f"wrote {len(files)} files to {out}: {len(projects)} project(s), {ok} readable, "
          f"{sum(1 for p in projects if p['flags'])} with flags")


if __name__ == "__main__":
    main()
