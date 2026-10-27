# Daily brief settings

Read by `scripts/brief.py`. One `key: value` per line below; anything else in this file is ignored.
A missing or invalid value falls back to its default, and the brief says so.

The time and the days the brief runs are not set here. They live only in your operating system's
scheduler entry (`scripts/schedule.py`), so the script itself has no notion of time.

- initialized: no
- enabled: no
- retention_days: 14
- horizon_days: 14
- lookahead_days: 42
- top_per_project: 5
- agenda_kinds: none
- agenda_lead_days: 2
- weekly_enabled: no
- weekly_retention_weeks: 8
- weekly_top_tasks: 5

`initialized` (`yes` or `no`, default `no`): read by the model, not by `brief.py`. While `no`, the first
session in this hub walks you through setup (`/fleet` → Setup). It becomes `yes` once you have
answered or declined each offer, so setup is not offered again.

`enabled` (`yes` or `no`, default `no`): whether the scheduled run and the catch-up on opening the hub
write a brief. Running `python scripts/brief.py` by hand always writes one.

`retention_days` (default 14): dated briefs (`_overview/brief-YYYY-MM-DD.md`) older than this many days
are deleted. `brief-latest.md` is always kept.

`horizon_days` (default 14): how far ahead "due soon" looks.

`lookahead_days` (default 42): how far ahead "Key dates ahead" lists every open dated milestone after
the `horizon_days` window. Collisions and crowded weeks are checked across this whole span. Both day
settings are capped at 366.

`top_per_project` (default 5): how many startable tasks to list for each project.

`agenda_kinds` (`client`, `internal`, `both` or `none`; default `none`): which meetings from each project's
`4-Trackers/meetings.md` get an "agenda not set" reminder in the brief. A reminder is worked out on every run and
never stored as a task; it clears once the meeting has an agenda (its own, or its series' standing list), is marked
"no agenda needed", or is snoozed with "Remind me on" from the brief page. A hub set up before this key existed is
asked once (`/fleet` → Setup, step 4).

`agenda_lead_days` (default 2, capped at 10): how many business days before the meeting the reminder appears.
Business days are Monday to Friday less your time off in `~/.familiar/time-off.md`; public holidays are not known
to the hub, so set a day more if one falls in the way.

The weekly brief (`scripts/weekly.py`, written to `_overview/weekly-*.md` and `weekly-latest.html`) reads the
last three keys; `brief.py` ignores them.

`weekly_enabled` (`yes` or `no`, default `no`): whether the scheduled run (`schedule.py enable --brief weekly`)
and the catch-up on opening the hub write a weekly brief. Running `python scripts/weekly.py` by hand always writes one.

`weekly_retention_weeks` (default 8): dated weekly briefs older than this many weeks are deleted.

`weekly_top_tasks` (default 5): how many startable and blocked tasks to list for each project with a date in the week.
