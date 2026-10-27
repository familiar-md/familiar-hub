# Hub settings

Read by `scripts/fleet_overview.py`, `scripts/brief.py` and `scripts/schedule.py`. One `key: value`
line below; anything else in this file is ignored.

- categories: all
- stale_days: 30
- me: unset

`categories` (default `all`): which registry categories this hub covers, matched against the
`category` column of `~/.familiar/fleet.md`. Use one hub per category to keep them apart, for example
a hub with `categories: work` and another with `categories: personal`. A scoped hub never reads a
project outside its categories; an included project with no category (`unset` or blank) is named in
the digest and the brief so you can set it. A list (`work, personal`) or `all` covers several.

A scoped hub also gives its scheduler entry its own name (`familiar-daily-brief-work`), so two hubs
can each have a schedule.

`stale_days` (default 30): an active project whose status snapshot was last updated more than this many
days ago is marked STALE in the digest and the brief; one with no last-updated date is marked STALE?.
Pause or close a project with `fleet_overview.py --set-lifecycle` and it stops being flagged.

`me` (default `unset`): the name, or names separated by commas, that mean you in a task's or milestone's
Owner field, for example `me: Sam` or `me: Sam, SL`. The brief's page highlights those owners. Matched
as whole words, ignoring case.
