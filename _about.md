# familiar-hub

One place, outside every project, to ask what is open across all your familiar projects: tasks you
can start, what lands in the next two weeks, which projects are stale. It reads each registered
project and writes nothing into any of them.

**Status:** v1.0.0, first released alongside familiar core 1.0.0 and versioned on its own from there. Earlier private builds ran 0.2.0 to 0.10.2 in September and October 2026. One headless Claude Code run (2026-10-10, 15 projects): the skill loaded, ran the
script, and its overdue answer (6 milestones in 3 projects) matched the script's own output; nothing was
written outside the hub copy. The run was in an untrusted workspace, so the shipped permission allowlist
was ignored and is still unverified, and Codex and Gemini CLI have not been run. It is not a familiar project and
carries no `.familiar-project` marker.

## How it works

`~/.familiar/fleet.md` is the registry, a Markdown table of `name | path | category | included | added | lifecycle`.
`scripts/fleet_overview.py` (standard library only) runs each project's own `project_check.py`,
`task_frontier.py` and `timeline_views.py` with `--root --json`, falling back to the copies in
`scripts/bundled/` when a project's copy is missing or too old, reads the status snapshot for
status and last-updated date, and writes four files to `_overview/`: `digest.md`, `frontier.md`, `next-two-weeks.md`,
`health.md`. The `/fleet` skill answers from those, and has a documented manual path for when the
script cannot run.

Lifecycle (`active`, `paused`, `reference`, `closed`) belongs to the hub: it is the registry's
`lifecycle` column, blank means `active`, and `python scripts/fleet_overview.py --set-lifecycle NAME
VALUE` changes it (one row, after a dated backup). An active project whose snapshot is older than
`stale_days` (`hub-settings.md`, default 30) is marked STALE, and one with no last-updated date STALE?,
at the top of the digest and the brief. A snapshot `**Lifecycle:**` line that disagrees with the
registry is reported; the registry wins. A project joins or leaves the registry from inside that
project with `/system-settings` → Fleet.

## Using it

See `INSTALL-HUB.md`. Short form: put this folder somewhere outside your projects, open a coding host
in it, and run `/fleet`. `python scripts/fleet_overview.py --discover <dir>` lists familiar projects
under `<dir>` that are not registered yet; adding `--apply` appends them to the registry.

`scripts/bundled/` must stay byte-identical to the same files in `familiar-workspace/.familiar/scripts/`
(a gate enforces it). `_overview/` is generated output.

## One hub per category

`hub-settings.md` → `categories` (default `all`) limits a hub to registry rows of those categories, so
a `work` hub and a `personal` hub kept in separate folders never read, count or mention each other's
projects. Both read the same `~/.familiar/fleet.md`; a project whose row has no category is named in
the digest and brief, unread, until you set one. `--discover --apply` in a one-category hub files new
projects under that category, and each hub's schedule gets its own name.

## Updating projects from the hub

`/fleet` → update changes milestones, tasks and meetings across projects in one pass: a date moved, a deadline dropped
(`not applicable`, never deleted), a task finished, one meeting date moved or cancelled, a series ended. Meetings are edit-only:
the hub never creates one, and a meeting change ends with a reminder to update your own calendar. Your words become lines in `_overview/pending-changes.txt`;
`scripts/apply_change.py preview` dry-runs every line through that project's own `scripts/tracker_edit.py` (the
hub's bundled copy for a project behind on core) and shows before and after; `apply --yes` runs only after you say
yes, and only the lines you saw (a project that changed in between is refused, not overwritten). Each change checks
the file's revision, replaces it atomically, appends the old to new value and reason to the project's status-log, and
regenerates its timeline views. The hub never edits a project's snapshot, other tracker or downstream milestones: the
log says they are not yet reconciled, and the next session in that project finishes it. Only projects in this hub's
categories can be changed. This is the hub's third declared exception to read-only (see `AGENTS.md`).

## Daily brief

`scripts/brief.py` writes one short page, `_overview/brief-<date>.md` (copied to `brief-latest.md`):
overdue milestones, what is due in the next 14 days, every open dated milestone up to six weeks out,
days and weeks where several projects collide, the top startable tasks per project, and which
projects are stale or missing data, with each project's data age. The same run writes the facts as
`brief-latest.json` and pastes them into a fixed template, `scripts/brief-template.html`, to make
`brief-latest.html`: a page with no network access that any browser opens from disk, whatever coding
host you use. It ranks milestones as now (overdue or due within 3 days), watch (next 14 days or at
risk) and later, keeps paused projects folded at the bottom, shows each owner (your own name, set as `me`
in `hub-settings.md`, highlighted), explains an at-risk status from the project's own timeline note, and
shades your time off from `~/.familiar/time-off.md` (one file for every hub on the machine). The timeline draws each
milestone by its `Type`: a deliverable is a solid star, a client meeting a solid circle, an internal meeting an open circle, anything else
a solid diamond, all in the urgency colour (red now or overdue, gold later); each row shows the project's familiar signature; a week with 3 or more deliverables across the projects shown gets an amber ⚠ on its date. On milestones and startable tasks with an ID, the page also lets you mark done, move a date (with a reason) or
mark not needed (with a reason), rename it (Edit), and each meeting row can be renamed, moved, cancelled or given an agenda (with a reason where one applies); marks are kept in the bar at the bottom, not applied, and "Copy for the hub" gives lines to paste
into `/fleet` update. When a project keeps `4-Trackers/meetings.md`, the hub reads its planned internal and client meetings through that project's own `meetings_view.py` and the page shows each meeting as a thin, expandable row in Do now / Watch / Later (its agenda, an agenda-due flag with Set agenda / No agenda needed / Remind me on, and Copy deck prompt), small meeting shapes in a lower lane of that project's timeline row (with a Meetings toggle), a Next meeting line on its card, and overlap and time-off flags; the markdown brief lists the same. A project without the file adds nothing. The page writes nothing to any project. Settings are in `brief-settings.md`;
it ships turned off. Turned on, the brief is written the first time you open the hub each day, and
optionally on a schedule with `scripts/schedule.py` (Windows Task Scheduler, tested; macOS launchd
and Linux systemd or cron, written from their docs and not yet run). Besides the registry, the schedule
and `~/.familiar/time-off.md` are the only things the hub writes outside its own folder, each only
after you agree to it. Briefs contain project task
text: they stay in `_overview/`, which is gitignored and never shipped. On 2026-10-14 the brief
covered all 15 projects, a scheduled run fired and wrote it, and headless Claude Code sessions ran
the catch-up only when today's brief was missing.
