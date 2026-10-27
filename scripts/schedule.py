#!/usr/bin/env python3
"""schedule.py — turn the daily or weekly brief's operating-system schedule on or off. Optional.

    python scripts/schedule.py status
    python scripts/schedule.py enable --time 08:00 --days weekdays --print   # show it, apply nothing
    python scripts/schedule.py enable --time 08:00 --days weekdays --yes
    python scripts/schedule.py disable --yes
    python scripts/schedule.py enable --brief weekly --time 12:00 --days sun --yes   # the weekly brief
    python scripts/schedule.py status --brief weekly

`--brief daily` (default) schedules `brief.py`; `--brief weekly` schedules `weekly.py --scheduled` under its own
name (`familiar-weekly-brief`), so both can run side by side. The weekly entry sets `weekly_enabled: yes`.

`--days` is `weekdays`, `daily`, or a list such as `mon,wed,fri`. The entry runs
`<this interpreter> <hub>/scripts/brief.py --catch-up` with the absolute interpreter path found at
`enable` time, so a scheduler with a different PATH still finds Python, and `--catch-up` means a
run is skipped when the brief is disabled or today's brief already exists.

This writes outside the hub: one scheduler entry for the current user. That is the hub's one
declared exception to writing only inside itself, so `enable` and `disable` change nothing
without `--yes`, and `--yes` is for after the user has agreed. It never touches a project.
`enable` also sets `enabled: yes` in `brief-settings.md`, since a scheduled run of a disabled
brief does nothing; `disable` leaves that setting alone (catch-up on opening the hub still works).

One entry per operating system, all for the current user only. A hub scoped in `hub-settings.md`
appends its categories to every name below (`familiar-daily-brief-work`), so two hubs can coexist:

  Windows  Task Scheduler (`schtasks /Create /XML`), runs only while signed in, StartWhenAvailable
           for a missed run. Tested on Windows 11.
  macOS    a launchd agent, `~/Library/LaunchAgents/familiar.daily-brief.plist`, loaded with
           `launchctl bootstrap gui/<uid>`. A run missed while asleep happens on wake; one missed
           while the Mac was off does not. UNTESTED: written from Apple's docs, never run on a Mac.
  Linux    a systemd user timer (`~/.config/systemd/user/familiar-daily-brief.{service,timer}`,
           Persistent=true so a missed run happens at the next start) when `systemctl --user`
           answers, otherwise one crontab line marked `# familiar-daily-brief` (cron never makes
           up a missed run). UNTESTED: written from the systemd and crontab man pages.

`--print --target windows|macos|systemd|cron` renders another system's entry without applying it,
for checking or applying by hand. On every system, the catch-up when the hub is opened covers a
missed run. Standard library only.
"""

# familiar's floor is Python 3.10. On anything older, stop with a plain message instead of a traceback.
import sys
if sys.version_info < (3, 10):
    sys.exit("familiar needs Python 3.10 or newer, but this is Python %d.%d (%s). Install Python 3.10+ "
             "(.familiar/scripts/doctor.py says how), then run this again with it." % (*sys.version_info[:2], sys.executable))

import argparse
import csv
import io
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from xml.sax.saxutils import escape

HUB = Path(__file__).resolve().parent.parent
BRIEF = HUB / "scripts" / "brief.py"
WEEKLY = HUB / "scripts" / "weekly.py"
SCRIPT = BRIEF  # the script the entry runs; `configure` switches it for the weekly brief
SCRIPT_ARG = "--catch-up"
KIND = "daily"
ENABLED_KEY = "enabled"
SETTINGS = HUB / "brief-settings.md"
TASK_NAME = "familiar-daily-brief"
LABEL = "familiar.daily-brief"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
CRON_MARK = "# familiar-daily-brief"
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
XML_DAY = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
           "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}


# ---------------------------------------------------------------- arguments

def parse_time(text: str) -> str:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("time must be HH:MM, 24-hour, for example 08:00")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_days(text: str) -> list[str]:
    text = text.strip().lower()
    if text == "daily":
        return list(DAYS)
    if text == "weekdays":
        return DAYS[:5]
    days = [d.strip()[:3] for d in text.split(",") if d.strip()]
    bad = [d for d in days if d not in DAYS]
    if bad or not days:
        raise argparse.ArgumentTypeError("days must be weekdays, daily, or a list like mon,wed,fri")
    return [d for d in DAYS if d in days]


def interpreter() -> Path:
    """The interpreter running this script; on Windows its windowless twin, so no console flashes."""
    exe = Path(sys.executable).resolve()
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        quiet = exe.with_name("pythonw.exe")
        if quiet.is_file():
            return quiet
    return exe


def configure(kind: str):
    """Point every backend at the daily or the weekly brief: script, argument, entry names, settings key."""
    global SCRIPT, SCRIPT_ARG, KIND, ENABLED_KEY, TASK_NAME, LABEL, PLIST, CRON_MARK
    KIND = kind
    SCRIPT, SCRIPT_ARG = (WEEKLY, "--scheduled") if kind == "weekly" else (BRIEF, "--catch-up")
    ENABLED_KEY = "weekly_enabled" if kind == "weekly" else "enabled"
    TASK_NAME = f"familiar-{kind}-brief"
    LABEL = f"familiar.{kind}-brief"
    PLIST = PLIST.with_name(f"{LABEL}.plist")
    CRON_MARK = f"# {TASK_NAME}"


def use_scope_names() -> str:
    """Give a scoped hub's entry its own name, so a work hub and a personal hub can both be scheduled."""
    global TASK_NAME, LABEL, PLIST, CRON_MARK
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fleet_overview
    scope = fleet_overview.read_scope()
    if scope:
        suffix = "-".join(sorted(re.sub(r"[^a-z0-9]+", "", c) for c in scope))
        TASK_NAME = f"{TASK_NAME}-{suffix}"
        LABEL = f"{LABEL}.{suffix}"
        PLIST = PLIST.with_name(f"{LABEL}.plist")
        CRON_MARK = f"# {TASK_NAME}"
    return TASK_NAME


def interpreter_for(target: str):
    """For --print of another system's entry: a placeholder interpreter path for that system."""
    if target == detect():
        return interpreter()
    return PureWindowsPath(r"C:\Path\To\pythonw.exe") if target == "windows" else PurePosixPath("/usr/bin/python3")


# ---------------------------------------------------------------- Windows: Task Scheduler

def windows_xml(time: str, days: list[str], exe: Path) -> str:
    start = f"{date.today().isoformat()}T{time}:00"
    if len(days) == 7:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        listed = "".join(f"<{XML_DAY[d]} />" for d in days)
        schedule = (f"<ScheduleByWeek><DaysOfWeek>{listed}</DaysOfWeek>"
                    "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>familiar {KIND} brief: writes {escape(str(HUB / "_overview"))}\\{"weekly" if KIND == "weekly" else "brief"}-latest.md. Created by scripts/schedule.py; remove with schedule.py disable.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      {schedule}
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(exe))}</Command>
      <Arguments>{escape(f'"{SCRIPT}" {SCRIPT_ARG}')}</Arguments>
      <WorkingDirectory>{escape(str(HUB))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace")


def windows_enable(time, days, exe, dry: bool) -> int:
    xml = windows_xml(time, days, exe)
    if dry:
        print(f'Would run: schtasks /Create /TN "{TASK_NAME}" /XML <file> /F\nwith this task definition:\n')
        print(xml)
        print("To apply by hand: save the XML above as UTF-16, then run that command with its path, or "
              "import it in Task Scheduler (Action > Import Task).")
        return 0
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(path).write_text(xml, encoding="utf-16")
        proc = schtasks("/Create", "/TN", TASK_NAME, "/XML", path, "/F")
    finally:
        os.unlink(path)
    print((proc.stdout or proc.stderr).strip())
    return proc.returncode


def windows_disable(dry: bool) -> int:
    if dry:
        print(f'Would run: schtasks /Delete /TN "{TASK_NAME}" /F')
        return 0
    if schtasks("/Query", "/TN", TASK_NAME).returncode != 0:
        print(f"No task named {TASK_NAME}; nothing to remove.")
        return 0
    proc = schtasks("/Delete", "/TN", TASK_NAME, "/F")
    print((proc.stdout or proc.stderr).strip())
    return proc.returncode


def windows_status() -> int:
    proc = schtasks("/Query", "/TN", TASK_NAME, "/XML")
    if proc.returncode != 0:
        print(f"Not scheduled: no Task Scheduler task named {TASK_NAME}.")
        return 0
    xml = proc.stdout

    def tag(name):
        m = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return m.group(1).strip() if m else None

    command, arguments = tag("Command"), tag("Arguments") or ""
    days = re.findall(r"<(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*/>", xml)
    when = (tag("StartBoundary") or "")[11:16]
    print(f"Scheduled: Task Scheduler task {TASK_NAME}, at {when or '?'} on "
          f"{', '.join(d[:3] for d in days) if days else 'every day'}.")
    print(f"Runs: {command} {arguments}")
    print(f"Interpreter: {'present' if command and Path(command).is_file() else 'MISSING — run enable again'}")
    script = re.search(rf'"([^"]+{re.escape(SCRIPT.name)})"', arguments)
    if script and Path(script.group(1)).resolve() != SCRIPT:
        print(f"Note: the task runs a {SCRIPT.name} in another folder ({script.group(1)}), not this hub.")
    elif script and not Path(script.group(1)).is_file():
        print(f"{SCRIPT.name}: MISSING at that path — run enable again from the hub's current location")
    # Next and last run come from the verbose listing; its field names follow the Windows display
    # language, so they are shown only when the English names are present.
    verbose = schtasks("/Query", "/TN", TASK_NAME, "/V", "/FO", "CSV")
    try:
        row = next(csv.DictReader(io.StringIO(verbose.stdout)))
        for key in ("Next Run Time", "Last Run Time", "Last Result", "Status"):
            if key in row:
                value = row[key]
                if key == "Last Result":
                    value += {"0": " (succeeded)", "267011": " (has not run yet)",
                              "267009": " (running now)"}.get(value.strip(), " (the brief script failed; see the latest brief file)")
                print(f"{key}: {value}")
    except StopIteration:
        pass
    return 0


# ---------------------------------------------------------------- macOS: launchd agent (UNTESTED)

LAUNCHD_DAY = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def gui_domain() -> str:
    return f"gui/{os.getuid()}" if hasattr(os, "getuid") else "gui/<uid>"


def macos_plist(time, days, exe) -> bytes:
    hour, minute = (int(x) for x in time.split(":"))
    if len(days) == 7:
        when = {"Hour": hour, "Minute": minute}
    else:
        when = [{"Weekday": LAUNCHD_DAY[d], "Hour": hour, "Minute": minute} for d in days]
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(exe), str(SCRIPT), SCRIPT_ARG],
        "WorkingDirectory": str(HUB),
        "StartCalendarInterval": when,
    })


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, errors="replace")


def macos_enable(time, days, exe, dry: bool) -> int:
    body = macos_plist(time, days, exe)
    if dry:
        print(f"Would write {PLIST} (mode 600):\n")
        print(body.decode())
        print(f"Then: launchctl bootout {gui_domain()}/{LABEL}   (only if already loaded)\n"
              f"      launchctl bootstrap {gui_domain()} {PLIST}")
        return 0
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    launchctl("bootout", f"{gui_domain()}/{LABEL}")  # replaces an earlier entry; fails harmlessly if none
    PLIST.write_bytes(body)
    PLIST.chmod(0o600)  # Apple: agents must not be group or world writable
    proc = launchctl("bootstrap", gui_domain(), str(PLIST))
    print((proc.stdout or proc.stderr).strip() or f"Loaded {LABEL} from {PLIST}.")
    return proc.returncode


def macos_disable(dry: bool) -> int:
    if dry:
        print(f"Would run: launchctl bootout {gui_domain()}/{LABEL}\nthen delete {PLIST}")
        return 0
    if not PLIST.is_file():
        print(f"No {PLIST}; nothing to remove.")
        return 0
    launchctl("bootout", f"{gui_domain()}/{LABEL}")
    PLIST.unlink()
    print(f"Unloaded {LABEL} and deleted {PLIST}.")
    return 0


def macos_status() -> int:
    if not PLIST.is_file():
        print(f"Not scheduled: no {PLIST}.")
        return 0
    data = plistlib.loads(PLIST.read_bytes())
    when = data.get("StartCalendarInterval") or {}
    items = when if isinstance(when, list) else [when]
    names = {v: k for k, v in LAUNCHD_DAY.items()}
    first = items[0] if items else {}
    days = ", ".join(names.get(i["Weekday"] % 7, "?") for i in items if "Weekday" in i) or "every day"
    print(f"Scheduled: launchd agent {LABEL}, at {first.get('Hour', 0):02d}:{first.get('Minute', 0):02d} on {days}.")
    args = data.get("ProgramArguments", [])
    print(f"Runs: {' '.join(shlex.quote(a) for a in args)}")
    print(f"Interpreter: {'present' if args and Path(args[0]).is_file() else 'MISSING — run enable again'}")
    loaded = launchctl("print", f"{gui_domain()}/{LABEL}").returncode == 0
    print(f"Loaded in launchd: {'yes' if loaded else 'no — run enable again'}")
    return 0


# ---------------------------------------------------------------- Linux: systemd user timer (UNTESTED)

SYSTEMD_DAY = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}


def unit_quote(text: str) -> str:
    """One quoted unit-file argument: `%` starts a specifier there, so it is doubled."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def systemd_units(time, days, exe) -> dict[str, str]:
    weekdays = "" if len(days) == 7 else ",".join(SYSTEMD_DAY[d] for d in days) + " "
    service = (f"[Unit]\nDescription=familiar {KIND} brief\n\n[Service]\nType=oneshot\n"
               f"WorkingDirectory={unit_quote(str(HUB))}\n"
               f"ExecStart={unit_quote(str(exe))} {unit_quote(str(SCRIPT))} {SCRIPT_ARG}\n")
    timer = (f"[Unit]\nDescription=familiar {KIND} brief schedule (created by scripts/schedule.py)\n\n"
             f"[Timer]\nOnCalendar={weekdays}*-*-* {time}:00\nPersistent=true\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return {f"{TASK_NAME}.service": service, f"{TASK_NAME}.timer": timer}


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, errors="replace")


def systemd_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        return systemctl("show-environment").returncode == 0
    except OSError:
        return False


def systemd_enable(time, days, exe, dry: bool) -> int:
    units = systemd_units(time, days, exe)
    if dry:
        for name, body in units.items():
            print(f"Would write {UNIT_DIR / name}:\n\n{body}")
        print(f"Then: systemctl --user daemon-reload\n      systemctl --user enable --now {TASK_NAME}.timer")
        print("A user timer runs while your user manager is running (normally while you are logged in); "
              "`loginctl enable-linger` keeps it running after logout. This script does not set that.")
        return 0
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in units.items():
        (UNIT_DIR / name).write_text(body, encoding="utf-8", newline="\n")
    systemctl("daemon-reload")
    proc = systemctl("enable", "--now", f"{TASK_NAME}.timer")
    print((proc.stdout or proc.stderr).strip() or f"Enabled {TASK_NAME}.timer.")
    return proc.returncode


def systemd_disable(dry: bool) -> int:
    files = [UNIT_DIR / f"{TASK_NAME}.timer", UNIT_DIR / f"{TASK_NAME}.service"]
    if dry:
        print(f"Would run: systemctl --user disable --now {TASK_NAME}.timer\nthen delete "
              + " and ".join(str(f) for f in files) + ", then systemctl --user daemon-reload")
        return 0
    if not any(f.is_file() for f in files):
        print(f"No {TASK_NAME} units in {UNIT_DIR}; nothing to remove.")
        return 0
    systemctl("disable", "--now", f"{TASK_NAME}.timer")
    for f in files:
        if f.is_file():
            f.unlink()
    systemctl("daemon-reload")
    print(f"Disabled and deleted {TASK_NAME}.timer and .service.")
    return 0


def systemd_status() -> int:
    timer, service = UNIT_DIR / f"{TASK_NAME}.timer", UNIT_DIR / f"{TASK_NAME}.service"
    if not timer.is_file():
        print(f"Not scheduled: no {timer}.")
        return 0
    cal = re.search(r"^OnCalendar=(.+)$", read_text(timer), re.MULTILINE)
    print(f"Scheduled: systemd user timer {TASK_NAME}.timer, OnCalendar={cal.group(1) if cal else '?'}.")
    exec_line = re.search(r'^ExecStart="((?:[^"\\]|\\.)*)"(.*)$', read_text(service), re.MULTILINE)
    if exec_line:
        exe = exec_line.group(1).replace("%%", "%")
        print(f"Runs: {exe}{exec_line.group(2)}")
        print(f"Interpreter: {'present' if Path(exe).is_file() else 'MISSING — run enable again'}")
    listing = systemctl("list-timers", f"{TASK_NAME}.timer", "--no-pager")
    print(listing.stdout.strip() or listing.stderr.strip())
    return 0


# ---------------------------------------------------------------- Linux fallback: crontab (UNTESTED)

CRON_DAY = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def cron_line(time, days, exe) -> str:
    hour, minute = (int(x) for x in time.split(":"))
    dow = "*" if len(days) == 7 else ",".join(str(CRON_DAY[d]) for d in days)
    # crontab(5): an unescaped % in the command field becomes a newline.
    command = (f"cd {shlex.quote(str(HUB))} && {shlex.quote(str(exe))} {shlex.quote(str(SCRIPT))} {SCRIPT_ARG}"
               ).replace("%", "\\%")
    return f"{minute} {hour} * * {dow} {command} {CRON_MARK}"


def is_ours(line: str) -> bool:
    # Matched at the end only: `# familiar-daily-brief` is a prefix of a scoped hub's `...-work` mark.
    return line.rstrip().endswith(CRON_MARK)


def crontab_read() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, errors="replace")
    return proc.stdout.splitlines() if proc.returncode == 0 else []  # non-zero: no crontab yet


def crontab_write(lines: list[str]) -> int:
    proc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", capture_output=True, text=True)
    if proc.returncode:
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def cron_enable(time, days, exe, dry: bool) -> int:
    line = cron_line(time, days, exe)
    if dry:
        print(f"Would add this line to your crontab, replacing any line ending {CRON_MARK}:\n\n{line}\n")
        print("cron does not make up a run missed while the computer was off or asleep.")
        return 0
    if not shutil.which("crontab"):
        print("No `crontab` command found; nothing changed.", file=sys.stderr)
        return 2
    code = crontab_write([l for l in crontab_read() if not is_ours(l)] + [line])
    if code == 0:
        print(f"Added to crontab: {line}")
    return code


def cron_disable(dry: bool) -> int:
    if dry:
        print(f"Would remove the crontab line ending {CRON_MARK}.")
        return 0
    lines = crontab_read()
    kept = [l for l in lines if not is_ours(l)]
    if len(kept) == len(lines):
        print(f"No crontab line marked {CRON_MARK}; nothing to remove.")
        return 0
    code = crontab_write(kept)
    if code == 0:
        print("Removed the crontab line.")
    return code


def cron_status() -> int:
    line = next((l for l in crontab_read() if is_ours(l)), None)
    if line is None:
        print(f"Not scheduled: no crontab line marked {CRON_MARK}.")
        return 0
    print(f"Scheduled: crontab line\n  {line}")
    command = line.split(" && ", 1)[1].replace(CRON_MARK, "") if " && " in line else ""
    parts = shlex.split(command) if command else []
    print(f"Interpreter: {'present' if parts and Path(parts[0]).is_file() else 'MISSING or unreadable — run enable again'}")
    return 0


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


BACKENDS = {
    "windows": (windows_enable, windows_disable, windows_status),
    "macos": (macos_enable, macos_disable, macos_status),
    "systemd": (systemd_enable, systemd_disable, systemd_status),
    "cron": (cron_enable, cron_disable, cron_status),
}


def detect() -> str | None:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "systemd" if systemd_available() else "cron"
    return None


# ---------------------------------------------------------------- settings

def set_enabled() -> str:
    """Set this brief's enabled key to yes in brief-settings.md. A missing weekly key is appended, since hubs
    installed before the weekly brief do not have it; a missing daily key is only reported."""
    key = ENABLED_KEY
    line = re.compile(rf"^(\s*[-*]?\s*`?{key}`?\s*:\s*)`?\w+`?\s*$", re.MULTILINE)
    try:
        text = SETTINGS.read_text(encoding="utf-8")
    except OSError:
        return f"Note: {SETTINGS.name} not found; the scheduled run will skip until it says `{key}: yes`."
    if not line.search(text):
        if KIND != "weekly":
            return f"Note: {SETTINGS.name} has no `{key}:` line; add `- {key}: yes` or scheduled runs will skip."
        SETTINGS.write_text(text.rstrip("\n") + f"\n- {key}: yes\n", encoding="utf-8", newline="\n")
        return f"Added `{key}: yes` to {SETTINGS.name}."
    new = line.sub(lambda m: m.group(1) + "yes", text, count=1)
    if new != text:
        SETTINGS.write_text(new, encoding="utf-8", newline="\n")
        return f"Set `{key}: yes` in {SETTINGS.name}."
    return f"{SETTINGS.name} already has `{key}: yes`."


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    en = sub.add_parser("enable", help="create or replace the scheduler entry")
    en.add_argument("--time", type=parse_time, required=True, help="HH:MM, 24-hour, local time")
    en.add_argument("--days", type=parse_days, default=parse_days("weekdays"),
                    help="weekdays (default), daily, or a list like mon,wed,fri")
    for p in (en, sub.add_parser("disable", help="remove the scheduler entry")):
        p.add_argument("--brief", choices=("daily", "weekly"), default="daily", help="which brief's entry (default daily)")
        p.add_argument("--print", dest="dry", action="store_true", help="show what would be done; apply nothing")
        p.add_argument("--yes", action="store_true", help="the user has agreed to this change outside the hub")
        p.add_argument("--target", choices=sorted(BACKENDS), help="with --print: render another system's entry")
    st = sub.add_parser("status", help="report the scheduler entry and whether its interpreter still exists")
    st.add_argument("--brief", choices=("daily", "weekly"), default="daily", help="which brief's entry (default daily)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    configure(args.brief)
    use_scope_names()
    chosen = getattr(args, "target", None)
    if chosen and not args.dry:
        print("--target works only with --print: an entry is applied only on the system it is for.", file=sys.stderr)
        return 2
    target = chosen or detect()
    if target is None:
        print(f"schedule.py: {sys.platform} has no supported scheduler. Run `python scripts/brief.py` by hand, "
              "or rely on the catch-up when you open the hub.", file=sys.stderr)
        return 2
    enable, disable, status = BACKENDS[target]
    if args.cmd == "status":
        return status()
    if not args.dry and not args.yes:
        print(f"This {'creates' if args.cmd == 'enable' else 'removes'} a scheduler entry outside the hub "
              "folder. Nothing was changed. Rerun with --print to see it, or with --yes once the user has agreed.")
        return 2
    if chosen and chosen != detect():
        print(f"Preview for {chosen}, rendered on {sys.platform}: the interpreter is a placeholder and the hub "
              "paths are this machine's. On that system, run this without --target.\n")
    if args.cmd == "enable":
        exe = interpreter_for(target) if chosen else interpreter()
        code = enable(args.time, args.days, exe, args.dry)
        if code == 0 and not args.dry:
            print(set_enabled())
            print(f"Runs {', '.join(args.days)} at {args.time} with {exe}.")
        return code
    return disable(args.dry)


if __name__ == "__main__":
    sys.exit(main())
