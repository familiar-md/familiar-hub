# Installing the fleet hub

For using it once installed (what to ask, the brief page, changing projects from the hub), see
`MANUAL-HUB.md`.

The hub is a folder you open on its own, next to your projects, never inside one.

1. Copy this folder to a place outside every project, for example `Documents/Claude Code/_hub/`. Leave
   out `_overview/` if present; the briefs in it are regenerated, and it also holds `change-log.md` and `agenda-snoozes.md`, which are not.
2. From the hub folder, run `python scripts/fleet_overview.py --discover <your projects folder> --apply`.
   It creates `~/.familiar/fleet.md`, the list of your projects, if it does not exist yet, and adds
   every familiar project it finds there (run it once per folder). Only the hub creates this file;
   afterwards each project can add, remove or update its own row with `/system-settings` → Fleet.
   Without `--apply` it only lists what it found. On macOS and Linux
   the command is usually `python3`; use it wherever this file says `python`.
3. To keep work and personal apart, make two copies, for example `Claude Code/work/_hub/` and
   `Claude Code/personal/_hub/`, and set `categories: work` in one copy's `hub-settings.md` and
   `categories: personal` in the other's. Each must match the `category` column in `fleet.md`. A
   single hub with `categories: all` (the default) shows everything together.
4. Open your coding host in the hub folder and run `/fleet`. On Claude Code, accept the workspace-trust
   prompt first; the shipped `.claude/settings.json` pre-approves only the three read-only forms of
   the overview script, as `python` and as `python3`, and Claude Code applies it after that trust prompt.
   It also pre-approves `apply_change.py preview` (changes nothing in a project) and never `apply`: every
   `/fleet` → update batch is shown to you and needs your yes before it is applied.

## What the hub reads, and how to keep a project out

For each project in `~/.familiar/fleet.md` with `included: yes` (and, in a scoped hub, a matching
`category`), the hub reads the status snapshot (`5-System/Memory/status-snapshot.md`), tasks,
timeline and meetings (`4-Trackers/`), through that project's own scripts. It also runs the
project's health check, but reports problems in its inputs, drafts and knowledge only as counts
("3 drafts issue(s)"), never by file name. It never reads `1-Inputs/`, `2-Drafts/` or
`3-Knowledge/`, and writes to a project only through `/fleet` → update, after you say yes.

Whatever the hub reads goes to the AI assistant you run it in, like anything else that assistant
reads. To keep a project out of the hub entirely, set its `included` cell to `no` in
`~/.familiar/fleet.md`, or run `/system-settings` → Fleet inside that project to unregister it. An
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
     catches up a run missed while the PC was off or asleep. Tested on Windows 11.
   - macOS: a launchd agent in `~/Library/LaunchAgents/`. A run missed while asleep happens on wake;
     one missed while off does not. Not yet run on a Mac.
   - Linux: a systemd user timer that makes up a missed run, or a crontab line where systemd's user
     manager is not available (cron does not make up missed runs). Not yet run on Linux.
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

## What is confirmed and what is not

Checked against the Claude Code permissions documentation (2026-10-09):

- Reading files in a directory added with `permissions.additionalDirectories`, `--add-dir` or
  `/add-dir` needs no prompt; editing follows the permission mode.
- `additionalDirectories` in a project's `.claude/settings.json` waits for the workspace-trust prompt.
- That setting grants file access only. It does not load skills or `CLAUDE.md` from those folders, which
  is fine here because the hub reads projects as data.
- The script path needs none of this: running `python scripts/fleet_overview.py` is a shell command, and
  the script reads project folders itself. Only the manual fallback uses the file tools on a project, so
  only it prompts per folder unless you add the directories.

To avoid prompts on the manual path, add your projects folder to `.claude/settings.local.json`. On
Windows, forward slashes work in the path (backslashes would need doubling as `\`):

```json
{ "permissions": { "additionalDirectories": ["C:/Users/you/Documents/Claude Code"] } }
```

On macOS or Linux:

```json
{ "permissions": { "additionalDirectories": ["/Users/you/Documents/Claude Code"] } }
```

The hub ships no such entry because the path is yours. The docs do not say whether `~` is expanded in
that key, so use an absolute path. Their one example is a relative path (`"../docs/"`); how it resolves
is not documented, so prefer absolute.

Not confirmed:

- Codex and Gemini behaviour for any of the above.
- Reads and writes under `~/.familiar/` from a project. Codex's default sandbox may block writes
  outside the workspace, and Gemini CLI's file tools may be limited to workspace directories; neither
  was tried. When a host cannot write there, the `/system-settings` → Fleet branch stops and gives you
  the registry row to paste into `~/.familiar/fleet.md` yourself. Nothing covers a blocked read of
  `~/.familiar/` preferences.
- The hub in a live session on Codex or Gemini CLI. Claude Code has had one headless run (see
  `_about.md`).
