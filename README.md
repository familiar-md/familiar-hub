# familiar-hub

One view across all your [familiar](https://github.com/familiar-md/familiar) projects. The hub is a
separate folder, kept next to your projects, that reads every one of them and answers what is
overdue, what lands in the next two weeks and what you can start today. It can also write you a daily
and a weekly brief.

It reads each project through that project's own scripts: the status snapshot, tasks, timeline and
meetings. It never reads `1-Inputs/`, `2-Drafts/` or `3-Knowledge/`, and it writes into a project only
when you approve a change it has shown you. Whatever it reads goes to the AI assistant you run it in.
To keep a project out, set it to `included: no` in `~/.familiar/fleet.md`.

## Start

1. Download this repository and put the folder somewhere outside every project, for example next to
   them.
2. From the hub folder, run `python scripts/fleet_overview.py --discover <your projects folder> --apply`.
   It finds your familiar projects and lists them in `~/.familiar/fleet.md`.
3. Open Claude Code, Codex or Gemini CLI in the hub folder and run `/ili-fleet` (Claude Code) or
   `/fleet`. The first session walks you through setup.

## Read next

- [`INSTALL-HUB.md`](INSTALL-HUB.md): setup, what the hub reads, scheduling the briefs, host permissions.
- [`MANUAL-HUB.md`](MANUAL-HUB.md): what to ask, the brief page, changing projects from the hub.

Needs Python 3.10 or newer. A project on an older familiar core still works: the hub falls back to its own
copies of the project scripts. Scheduling is tested on
Windows; the macOS and Linux entries are written from their documentation and not yet run.

Apache 2.0, like familiar core. See [`LICENSE`](LICENSE).
