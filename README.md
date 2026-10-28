# familiar-hub

One view across all your [familiar](https://github.com/familiar-md/familiar) projects. The hub is a
separate folder, kept next to your projects, that reads every one of them and answers what is
overdue, what lands in the next two weeks and what you can start today. It can also write you a daily
and a weekly brief.

It reads each project through that project's own scripts: the status snapshot, tasks, timeline and
meetings. It never reads `1-Inputs/`, `2-Drafts/` or `3-Knowledge/`, and it writes into a project only
when you approve a change it has shown you. Whatever it reads goes to the AI assistant you run it in.

## Before you start

You need at least one familiar project already set up: made from the familiar workspace and checked in
at least once. The hub has nothing to show until a project exists. You also need Python 3.10 or newer.

## Start

1. Download `familiar-hub-<version>.zip` from
   [Releases](https://github.com/familiar-md/familiar-hub/releases) and unzip it. The `familiar-hub`
   folder inside is your hub: put it somewhere outside every project, for example next to them, and
   rename it if you like.
2. Open your AI assistant in the hub folder and run `/ili-fleet` (Claude Code) or `/fleet`. The first
   session sets the hub up with you: it asks where your projects are, finds them, and offers the daily
   brief. Nothing is changed without your yes.

If you would rather do the first step by hand, run this from the hub folder:
`python scripts/fleet_overview.py --discover <your projects folder> --apply`.

## One hub or two

One hub shows every project. To keep work and personal projects apart, make two hub folders and give
each a category (see [`INSTALL-HUB.md`](INSTALL-HUB.md)). Both hubs read the same list of projects,
`~/.familiar/fleet.md` in your home folder, because that is the one place a project can register
itself. The category on each row is how a hub knows which half of that list is its own.

## Read next

- [`INSTALL-HUB.md`](INSTALL-HUB.md): setup, updating, what the hub reads, scheduling the briefs.
- [`MANUAL-HUB.md`](MANUAL-HUB.md): what to ask, the brief page, changing projects from the hub.

Tested with Claude Code on Windows 11.

Apache 2.0, like familiar core. See [`LICENSE`](LICENSE).
