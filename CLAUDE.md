# familiar hub adapter for Claude

<!-- Host adapter for Claude Code. Adds no rules of its own -- see AGENTS.md. -->

@AGENTS.md

On this host the hub's command is `/ili-fleet`; the bare `/fleet` also works. Give users the `/ili-` form.

When you need answers from the user, such as check-in questions, setup offers or a choice between options, ask with this host's AskUserQuestion tool: one question per item, the likely answers as options (the user can always type their own), at most four per call. Ask in plain text when only the user can write the answer (a date, a name, a reason) or the tool is unavailable.
