# Installing the fleet hub

For using it once installed (what to ask, the brief page, changing projects from the hub), see
`MANUAL-HUB.md`.

The hub is a folder you open on its own, next to your projects, never inside one. Set up at least one
familiar project before you install it.

## Installing

1. Download `familiar-hub-<version>.zip` from the
   [Releases page](https://github.com/familiar-md/familiar-hub/releases) and unzip it. Put the
   `familiar-hub` folder somewhere outside every project, for example `Documents/Claude Code/_hub/`.
2. Open your AI assistant in the hub folder and run `/fleet` (`/ili-fleet` on Claude Code). On Claude
   Code, accept the workspace-trust prompt first. The first session walks you through setup: it asks
   which folder holds your projects, finds the familiar projects in it and lists them in
   `~/.familiar/fleet.md`, then offers the daily brief.

   To find the projects yourself instead, run this from the hub folder (`python3` on macOS and Linux):

   ```text
   python scripts/fleet_overview.py --discover <your projects folder> --apply
   ```

   Without `--apply` it only lists what it found. Run it once per folder that holds projects. Only the
   hub creates `~/.familiar/fleet.md` and changes it: later, ask `/fleet` to add, exclude or
   recategorise a project.
3. Optional: to keep work and personal apart, make two hub folders, for example
   `Claude Code/work/_hub/` and `Claude Code/personal/_hub/`. Set `categories: work` in one folder's
   `hub-settings.md` and `categories: personal` in the other's. Both read the same
   `~/.familiar/fleet.md`, and each row's `category` decides which hub it belongs to. A single hub with
   `categories: all` (the default) shows everything together.

The shipped `.claude/settings.json` lets the overview script run without a prompt, and lets
`apply_change.py preview` run (it changes nothing). It never pre-approves `apply`: every change to a
project is shown to you and needs your yes.

## Updating

Download the new zip and copy everything in it over your hub folder, except your two settings files
(`hub-settings.md`, `brief-settings.md`) and the `_overview/` folder. `_overview/` holds your briefs, the
change log and your agenda snoozes; the settings files hold your choices. Your project list lives in
`~/.familiar/fleet.md`, outside the hub, so an update never touches it.

## What the hub reads, and how to keep a project out

For each project in `~/.familiar/fleet.md` with `included: yes` (and, in a scoped hub, a matching
`category`), the hub reads the status snapshot (`5-System/Memory/status-snapshot.md`), tasks,
timeline and meetings (`4-Trackers/`), through that project's own scripts. It also runs the
project's health check, but reports problems in its inputs, drafts and knowledge only as counts
("3 drafts issue(s)"), never by file name. It never reads `1-Inputs/`, `2-Drafts/` or
`3-Knowledge/`, and writes to a project only through `/fleet` → update, after you say yes.

Whatever the hub reads goes to the AI assistant you run it in, like anything else that assistant
reads. To keep a project out of the hub entirely, ask `/fleet` to exclude it, or set its
`included` cell to `no` in `~/.familiar/fleet.md`. An
excluded project appears in no new brief, no `_overview/` page and no `/fleet` answer. Today's brief,
if written before you excluded it, is rebuilt without it the next time the hub opens or `/fleet` shows
the brief. Dated briefs from earlier days (`_overview/brief-<date>.md`, `weekly-<date>.md`) keep what
they held until `retention_days` prunes them; delete them by hand if that matters sooner. A separate hub
per category (step 3 above) keeps work and personal projects apart in the same way.

## The daily brief (optional)

1. Set `enabled: yes` in `brief-settings.md`. From then on, the first session you open in the hub each
   day writes `_overview/brief-latest.md` (`python scripts/brief.py --catch-up`) and shows it. Run
   `python scripts/brief.py` any time for a fresh one. Open `_overview/brief-latest.html` in a browser
   for the visual version.
2. To have it written at a fixed time even when you do not open the hub, preview the entry, then apply
   it:

   ```text
   python scripts/schedule.py enable --time 08:00 --days weekdays --print
   python scripts/schedule.py enable --time 08:00 --days weekdays --yes
   python scripts/schedule.py status
   python scripts/schedule.py disable --yes
   ```

   `--days` takes `weekdays`, `daily` or a list such as `mon,wed,fri`. Run it from each hub you want
   scheduled; a scoped hub's entry is named after its category (`familiar-daily-brief-work`). The entry is for your user
   only and runs the Python you ran `enable` with, by full path; re-run `enable` if you move the hub
   or reinstall Python (`status` says when the interpreter is missing).
   - Windows: a Task Scheduler task, `familiar-daily-brief`, that runs while you are signed in and
     catches up a run missed while the PC was off or asleep.
   - macOS: a launchd agent in `~/Library/LaunchAgents/`. A run missed while asleep happens on wake;
     one missed while off does not.
   - Linux: a systemd user timer that makes up a missed run, or a crontab line where systemd's user
     manager is not available (cron does not make up missed runs).
   - If your organization blocks scheduled tasks, use `--print` to see the entry and ask IT, or rely on
     the open-the-hub catch-up.

## The weekly brief (optional)

A week-ahead snapshot: cautions first (overdue carry-overs, at-risk or not-started items, same-day collisions,
heavy weeks, meeting clashes, your time off, tentative dates, stale projects), then each day, each project's open
tasks, and the dates just past the week. The window is the coming Monday to Friday when it runs on a Friday,
Saturday or Sunday, otherwise today through the next 7 days.

1. `python scripts/weekly.py` writes `_overview/weekly-latest.md` and `weekly-latest.html` any time.
2. To have it written on a schedule, for example Sunday at noon:

   ```text
   python scripts/schedule.py enable --brief weekly --time 12:00 --days sun --print
   python scripts/schedule.py enable --brief weekly --time 12:00 --days sun --yes
   python scripts/schedule.py status --brief weekly
   python scripts/schedule.py disable --brief weekly --yes
   ```

   `enable --brief weekly` sets `weekly_enabled: yes` in `brief-settings.md` and creates its own entry
   (`familiar-weekly-brief`), separate from the daily one. A run missed while the PC was off is made up by the
   scheduler where it supports that (Windows: yes), or by the catch-up when you next open the hub (writes one if
   none was written in the last 6 days, so the window then starts that day).

## Fewer permission prompts on Claude Code

The overview script reads your projects itself, so it needs no folder permissions. Only the manual
fallback, used when the script cannot run, opens project files with the assistant's file tools.
To avoid prompts on the manual path, add your projects folder to `.claude/settings.local.json`. On
Windows, forward slashes work in the path (backslashes would need doubling as `\`):

```json
{ "permissions": { "additionalDirectories": ["C:/Users/you/Documents/Claude Code"] } }
```

On macOS or Linux:

```json
{ "permissions": { "additionalDirectories": ["/Users/you/Documents/Claude Code"] } }
```

The hub ships no such entry because the path is yours. Use an absolute path.
