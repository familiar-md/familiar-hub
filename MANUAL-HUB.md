# familiar hub manual

How to use the fleet hub once it is installed: what to ask it, how to read the brief, how to change
dates and tasks from it, and what it reads. For installing it, scheduling the briefs and host
permissions, see `INSTALL-HUB.md`.

## What the hub is

A folder you open on its own, outside every project, that answers questions across all your familiar
projects: what is overdue, what lands in the next two weeks, what you can start now, which projects
have gone quiet. It reads each project through that project's own scripts and writes nothing into a
project unless you approve a change (see Changing projects from the hub).

The list of projects is `~/.familiar/fleet.md`, one row per project:

```text
| name | path | category | included | added | lifecycle |
```

The hub creates that file the first time you run `--discover --apply` (`INSTALL-HUB.md`, step 2). After
that, each project adds or removes its own row with `/system-settings` → Fleet.

## Asking it things

Open your coding host in the hub folder. On Claude Code the command is `/ili-fleet` (bare `/fleet` also
works); on other hosts, `/fleet` or just ask. The first session walks you through setup: the daily
brief, agenda reminders, which projects are paused or closed, and which owner names mean you. Each
offer waits for your answer, and declining is fine.

After that, ask in plain words. The hub picks the matching branch:

| You ask | Branch | Answered from |
|---|---|---|
| "Where is everything?", or no question | overview | `_overview/digest.md` |
| "What can I start today?" | tasks | `_overview/frontier.md` |
| "What lands in the next two weeks? Do any projects collide?" | timelines | `_overview/next-two-weeks.md` |
| "Which projects are stale or broken?" | health | `_overview/health.md` |
| "Show me today's brief" | brief | `_overview/brief-latest.md` |
| "What does next week look like?" | weekly | `_overview/weekly-latest.md` |
| "Move the client review on Atlas to 3 November" | update | the project's own trackers, after your yes |
| "I'm off 22 to 26 December" | time off | `~/.familiar/time-off.md`, after your yes |
| "Atlas is on hold" | lifecycle | the registry's `lifecycle` cell |

Every answer comes from files the scripts wrote, each stamped with its date and the revision of
every project file it was built from. If the stamp is not today, the hub reruns the script before
answering. When it has to read a project file directly, because the script could not run or the
digest cannot answer, it says so.

Missing data is reported as missing. A date written as "early December" or "Q4" is listed as undated
and never turned into a guess.

## Lifecycle and staleness

Each project is `active` (the default, shown by a blank cell), `paused`, `reference` or `closed`.
Lifecycle belongs to the hub, not the project: change it by asking, or with
`python scripts/fleet_overview.py --set-lifecycle "<name>" paused`. Overdue items in a paused or closed
project are shown for information, not as alarms.

An active project whose status snapshot is older than `stale_days` (default 30, in `hub-settings.md`)
is marked STALE. One with no last-updated date is marked STALE?. The cure is a session inside that
project; the hub cannot update a snapshot.

## The daily brief

`python scripts/brief.py` writes `_overview/brief-latest.md` and, from the same facts,
`brief-latest.html`, a page that opens in any browser with no network access. With `enabled: yes` in
`brief-settings.md`, the first session you open each day writes it for you, and `schedule.py` can
write it at a fixed time (`INSTALL-HUB.md`).

The page puts urgent items first:

- **Do now:** overdue, or due within 3 days.
- **Watch:** due in the next 14 days, or marked at risk.
- **Later:** everything dated up to six weeks out.

Below that comes a six-week timeline (Monday to Friday, your time off shaded), then one card per
project, with paused projects folded at the bottom. On the timeline a deliverable is a star, a client
meeting a solid circle, an internal meeting an open circle and anything else a diamond, red when due now
or overdue and gold when later. A week with three or more deliverables across projects gets a ⚠. Your
own name, set as `me` in `hub-settings.md`, is highlighted wherever it is an owner.

The brief also carries a data-age table. A brief stamped today but built from snapshots weeks old is
not current; read the ages, not just the stamp.

The weekly brief (`python scripts/weekly.py`) is the same idea for one week, cautions first: overdue
carry-overs, at-risk and not-started items, same-day collisions, meeting clashes, time off, stale
projects. Run on a Friday, Saturday or Sunday, it covers the coming Monday to Friday; on other days it
covers today and the next 7.

### Marks on the page

On any milestone, task or meeting with an ID, the page lets you mark it done, move a date, mark it not
needed, rename it, or set a meeting's agenda. Moves, not-needed and renames ask for a reason. The marks
collect in a bar at the bottom. The page itself changes nothing: **Copy for the hub** gives you lines to
paste into `/ili-fleet`, which then runs them as an update.

## Changing projects from the hub

The one way the hub writes into a project. Say what changed, for one project or several:

```text
On Atlas the client review moved to 3 November because the client asked; T-12 on Beacon is done.
```

1. The hub turns your words into change lines, one per item, and asks when an item or a reason is
   unclear. It never guesses an ID.
2. It previews the whole batch: each project, before and after, and anything that still depends on a
   moved date.
3. Nothing is written until you say yes to that preview. Change anything and it previews again.
4. Each line is applied by that project's own `tracker_edit.py`, which checks the file has not changed
   since the preview, writes it in one step, and logs the old and new value with your reason in the
   project's status log. A project changed in between is refused, not overwritten.

What it can change: a milestone's date, status, confirmed date or name; a task's done, reopened, date or
name; a meeting's date, time, series end or agenda. "No longer needed" sets a milestone to
`not-applicable` and deletes nothing. New rows, new meetings and owners are changes to make inside the
project, and the hub says so. After a meeting change, update your own calendar; the hub syncs nothing.

The project's snapshot and its downstream milestones are not reconciled from the hub. Its status log
says so, and the next session in that project finishes the job. Every applied line is also kept in
`_overview/change-log.md`.

## Time off

Your time off lives in `~/.familiar/time-off.md`, one list for every hub on this machine. Ask to add a
period and the hub shows the exact line (`- 2026-12-22 to 2026-12-26 — holidays`) before writing it.
The brief shades it on the timeline, flags any milestone due then, and skips it when counting business
days for agenda reminders. Public holidays are not known to the hub.

## What the hub reads, and keeping a project out

For each row with `included: yes` (and, in a scoped hub, a matching category), the hub reads the status
snapshot, tasks, timeline and meetings, through the project's own scripts. Its health check reports
problems with inputs, drafts and knowledge only as counts, never by file name. It never reads
`1-Inputs/`, `2-Drafts/` or `3-Knowledge/`. Whatever it reads goes to the AI assistant you run it in.

To keep a project out, set its `included` cell to `no`, or run `/system-settings` → Fleet inside the
project. The project then appears in no new overview, brief or answer. If today's brief was written
before you excluded it, the brief is rebuilt without it the next time the hub opens or you ask for the
brief. Dated briefs from earlier days keep what they held until `retention_days` prunes them.

To keep work and personal apart, run two hubs, one per category (`hub-settings.md` → `categories`).
Neither reads, counts or mentions the other's projects, even when asked.

## Settings

| File | Holds |
|---|---|
| `hub-settings.md` | `categories`, `stale_days`, `me` |
| `brief-settings.md` | `enabled`, `retention_days`, `horizon_days`, `lookahead_days`, `top_per_project`, `agenda_kinds`, `agenda_lead_days`, the `weekly_*` keys, `initialized` |
| `~/.familiar/fleet.md` | the projects, their category, `included` and lifecycle |
| `~/.familiar/time-off.md` | your time off |

Each settings file explains its keys. Change them by hand or by asking; the hub changes one only when
you ask. The days and time a brief runs live in your operating system's scheduler, not in a file here.

## When something is wrong

**"The registry could not be read" in the brief.** `~/.familiar/fleet.md` is missing or malformed. The
brief says what it found; `--discover <folder> --apply` recreates it.

**A project shows as unreadable.** Its path in the registry no longer exists, or a script in it failed.
The digest names which. Fix the path in `fleet.md`, or set the row's `included` to `no`.

**A project is missing from every answer.** Check that its row has `included: yes` and, in a scoped hub,
a category this hub covers. An included row with no category is named once in the digest and not read.

**The brief is old.** Check `python scripts/schedule.py status`. The open-the-hub catch-up writes one only
when `enabled: yes`. `python scripts/brief.py` always writes one.

**An update was refused.** The project changed after the preview, or the line named an ID that does not
exist. Ask for the preview again.

## Limits

- The hub is only as current as each project's snapshot and trackers. It reports their age but cannot
  refresh them.
- Overdue items, collisions and the timeline come from dates written as `YYYY-MM-DD`. Anything vaguer is
  listed as undated.
- One writer at a time: an update checks that a file has not changed since the preview, but it does
  not merge two edits made at once.
- Tested with Claude Code on Windows 11.
