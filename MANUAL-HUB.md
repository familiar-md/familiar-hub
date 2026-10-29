# familiar hub manual

This manual covers the fleet hub after you install it: what to ask it, how to read the brief, how to
change dates and tasks from it, and what it reads. `INSTALL-HUB.md` covers installation, brief
schedules and agent permissions.

## What the hub is

The hub is a folder that you open on its own, outside every project. It answers questions across all
your familiar projects:

- what is overdue
- what is due in the next two weeks
- what you can start now
- which projects have gone quiet

It reads each project through that project's own scripts. It writes nothing into a project unless
you approve a change (see [Changing projects from the hub](#changing-projects-from-the-hub)).

The list of projects is `~/.familiar/fleet.md`, with one row per project:

```text
| name | path | category | included | added | lifecycle |
```

The hub creates this file the first time you run `--discover --apply` (`INSTALL-HUB.md`, step 2).
After that, the hub keeps the list. Ask `/ili-fleet` to add a project, exclude one, or change its
category or lifecycle. To add a project, it runs `--discover` and asks before it runs `--apply`. Each
change edits one row of the list, after a dated backup.

## Asking it things

Open your agent in the hub folder. On Claude Code, the command is `/ili-fleet`. `/fleet` also works.
On other agents, use `/fleet` or ask in plain words.

The first session walks you through setup: the daily brief, agenda reminders, which projects are
paused or closed, and which owner names mean you. Each offer waits for your answer. You can say no to
any of them.

After that, ask in plain words. The hub picks the matching branch:

| You ask | Branch | Answered from |
|---|---|---|
| "Where is everything?", or no question | overview | `_overview/digest.md` |
| "What can I start today?" | tasks | `_overview/frontier.md` |
| "What is due in the next two weeks? Do any projects collide?" | timelines | `_overview/next-two-weeks.md` |
| "Which projects are stale or broken?" | health | `_overview/health.md` |
| "Show me today's brief" | brief | `_overview/brief-latest.md` |
| "What does next week look like?" | weekly | `_overview/weekly-latest.md` |
| "Move the client review on Atlas to 3 November" | update | the project's own trackers, after your yes |
| "I am off 22 to 26 December" | time off | `~/.familiar/time-off.md`, after your yes |
| "Atlas is on hold" | lifecycle | the `lifecycle` cell in the project list |
| "Add the projects in my Clients folder", "Leave Atlas out" | registry | the rows of the project list, after your yes |

Every answer comes from files that the scripts wrote. Each file carries its date and the revision of
every project file it came from. If the date is not today, the hub runs the script again before it
answers. Sometimes the hub must read a project file directly, because a script could not run or the
digest has no answer. It then tells you so.

The hub reports missing data as missing. A date such as "early December" or "Q4" is listed as
undated. The hub never turns it into a guess.

## Lifecycle and staleness

Each project is `active` (the default, shown by a blank cell), `paused`, `reference` or `closed`. The
hub, not the project, records the lifecycle. To change it, ask the hub, or run
`python scripts/fleet_overview.py --set-lifecycle "<name>" paused`. Overdue items in a paused or
closed project are shown for information, not as alarms.

An active project is marked STALE when its status snapshot is older than `stale_days` (default 30, in
`hub-settings.md`). A project with no last-updated date is marked STALE?. To fix either, run a session
inside that project. The hub cannot update a snapshot.

## The daily brief

`python scripts/brief.py` writes `_overview/brief-latest.md`. From the same facts, it writes
`brief-latest.html`, a page that opens in any browser without network access. If `enabled: yes` is
set in `brief-settings.md`, the first session you open each day writes the brief for you.
`schedule.py` can also write it at a fixed time (`INSTALL-HUB.md`).

The page puts urgent items first:

- **Do now:** overdue, or due within 3 days.
- **Watch:** due in the next 14 days, or marked at risk.
- **Later:** everything with a date up to six weeks ahead.

Below that is a six-week timeline (Monday to Friday, with your time off shaded). Then comes one card
per project, with paused projects folded at the bottom. On the timeline, the marks are:

- a star for a deliverable
- a solid circle for a client meeting
- an open circle for an internal meeting
- a diamond for anything else

A mark is red when it is due now or overdue, and gold when it is later. A week with three or more
deliverables across projects gets a ⚠. Your own name, set as `me` in `hub-settings.md`, is
highlighted wherever it is an owner.

The brief also has a data-age table. A brief dated today can come from snapshots that are weeks old.
Such a brief is not current, so read the ages, not only the date.

The weekly brief (`python scripts/weekly.py`) does the same for one week. It puts cautions first:
overdue carry-overs, items at risk or not started, collisions on the same day, meeting clashes, time
off and stale projects. On a Friday, Saturday or Sunday, it covers the coming Monday to Friday. On
other days, it covers today and the next 7 days.

### Marks on the page

On any milestone, task or meeting with an ID, the page lets you:

- mark it done
- move its date
- mark it not needed
- rename it
- set a meeting's agenda

A move, a not-needed mark or a rename asks for a reason. The marks collect in a bar at the bottom of
the page. The page itself changes nothing. **Copy for the hub** gives you lines to paste into
`/ili-fleet`, which then runs them as an update.

## Changing projects from the hub

This is the one way the hub writes into a project. Say what changed, for one project or several:

```text
On Atlas the client review moved to 3 November because the client asked; T-12 on Beacon is done.
```

1. The hub turns your words into change lines, one per item. If an item or a reason is not clear, it
   asks. It never guesses an ID.
2. It previews the whole batch: each project, before and after, and anything that still depends on a
   moved date.
3. It writes nothing until you say yes to the preview. If you change anything, it previews again.
4. Each line goes through that project's own `tracker_edit.py`. The script checks that the file did
   not change since the preview, writes it in one step, and logs the old and new value with your
   reason in the project's status log. If the project changed in between, the script refuses the
   line. It does not overwrite.

The hub can change:

- a milestone's date, status, confirmed date or name
- a task's done or reopened state, date or name
- a meeting's date, time, series end or agenda

"No longer needed" sets a milestone to `not-applicable` and deletes nothing. You add new rows, new
meetings and owners inside the project, and the hub tells you so. After a meeting change, update your
own calendar. The hub syncs nothing.

The hub does not bring the project's snapshot or later milestones up to date. The project's status
log says so, and the next session in that project finishes the job. `_overview/change-log.md` also
keeps every applied line.

## Time off

Your time off is in `~/.familiar/time-off.md`, one list for every hub on this computer. If you ask to
add a period, the hub shows the exact line (`- 2026-12-22 to 2026-12-26 — holidays`) before it writes
it. The brief shades the period on the timeline and flags any milestone due in it. It also skips the
period when it counts business days for agenda reminders. The hub does not know public holidays.

## What the hub reads, and keeping a project out

The hub reads each row with `included: yes`. In a hub limited to some categories, the row's category
must also match. For each such project, it reads the status snapshot, tasks, timeline and meetings,
through the project's own scripts.

It never reads `1-Inputs/`, `2-Drafts/` or `3-Knowledge/`. Its health check reports problems in
those folders only as counts, never by file name. Whatever the hub reads goes to the AI agent that
you run it in.

To keep a project out, ask `/ili-fleet` to exclude it (`--set-included "<name>" no`). You can also
set its `included` cell to `no` by hand. The project then appears in no new overview, brief or
answer. If today's brief came before you excluded the project, the hub writes the brief again without
it. This happens the next time the hub opens or you ask for the brief. Briefs from earlier days keep
what they held until `retention_days` removes them.

To keep work and personal projects apart, run two hubs, one per category (`hub-settings.md` →
`categories`). Neither hub reads, counts or mentions the other's projects, even when asked.

## Settings

| File | Holds |
|---|---|
| `hub-settings.md` | `categories`, `stale_days`, `me` |
| `brief-settings.md` | `enabled`, `retention_days`, `horizon_days`, `lookahead_days`, `top_per_project`, `agenda_kinds`, `agenda_lead_days`, the `weekly_*` keys, `initialized` |
| `~/.familiar/fleet.md` | the projects, their category, `included` and lifecycle |
| `~/.familiar/time-off.md` | your time off |

Each settings file explains its keys. Change them by hand or ask the hub. The hub changes a setting
only when you ask. The days and times that a brief runs are in your operating system's scheduler, not
in a file here.

## When something is wrong

**"The registry could not be read" in the brief.** `~/.familiar/fleet.md` is missing or damaged. The
brief says what it found. `--discover <folder> --apply` makes the file again.

**A project shows as unreadable.** Its path in the list no longer exists, or a script in the project
failed. The digest says which. Fix the path in `fleet.md`, or set the row's `included` to `no`.

**A project is missing from every answer.** Check that its row has `included: yes`. In a hub limited
to some categories, also check that the row has a category this hub covers. The digest names an
included row with no category once, and the hub does not read it.

**The brief is old.** Run `python scripts/schedule.py status`. When you open the hub, it writes a
missed brief only if `enabled: yes` is set. `python scripts/brief.py` always writes one.

**An update was refused.** The project changed after the preview, or the line named an ID that does
not exist. Ask for the preview again.

## Limits

- The hub is only as current as each project's snapshot and trackers. It reports their age, but it
  cannot update them.
- Overdue items, collisions and the timeline come from dates written as `YYYY-MM-DD`. Anything less
  exact is listed as undated.
- The hub supports one writer at a time. An update checks that a file did not change since the
  preview, but it does not merge two edits made at the same time.
- The hub is tested with Claude Code on Windows 11.
