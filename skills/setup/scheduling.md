# Scheduling the report

Never create anything until the user has clearly said yes. Never create a schedule silently.

There are two ways. Use the first when it is available.

**Primary: a Claude desktop scheduled task.** If you have a tool for creating scheduled tasks, use it after the user's yes. Default to weekly on Monday at 08:07 local time (or the project's frequency: daily or monthly). Avoid exact :00 and :30 times. The task prompt is: `Use the clipper run skill for the project '<name>'`. Then confirm in one sentence when the first run will happen. If the computer is off at the scheduled time, the run happens when it is next on and covers the whole missed period.

**Fallback: the computer's own scheduler.** Use this if the scheduled-task tool is unavailable, if creating or running the task fails, or when the user asks for the backup method. Explain it in one plain sentence ("This tells your computer to start the report every Monday at 08:07, with permission only to run Clipper and write in your project folder"), and do it only after an explicit yes.

Because the command is long and contains quotes, do not put it directly in the scheduler. First write a small wrapper script in the project folder holding the `claude -p` line, then point the scheduler at that script.

- Windows: write `run-clipper.cmd` in the project folder. Its one line is the `claude -p "Use the clipper run skill for the project '<name>'" --allowedTools ...` command. Inside the quoted `Bash(...)` argument, write the quotes around the plugin root as `\"` (backslash then double quote): `claude` reads its Windows arguments the CommandLineToArgvW way, where `\"` is a literal quote inside a quoted argument. This quoting is UNVERIFIED, like the rest of the template. Then register it: `schtasks /Create /SC WEEKLY /D MON /ST 08:07 /TN "Clipper - <name>" /TR "\"<project folder>\run-clipper.cmd\""`
- macOS: write `run-clipper.sh` in the project folder (same `claude -p` line, made executable), and a minimal launchd plist in `~/Library/LaunchAgents/` (label `com.clipper.<name>` with the project name's spaces replaced by hyphens; XML-escape `&`, `<` and `>` in the name and paths; `ProgramArguments` pointing at the script, `StartCalendarInterval` Weekday 1, Hour 8, Minute 7). Register it with `launchctl bootstrap gui/$(id -u) <plist path>` (the older `launchctl load <plist path>` also works).

The `--allowedTools` template in `docs/spikes/scheduled-run.md` is UNVERIFIED until the spike's fallback run has passed. Treat it as a starting point: check the flags with `claude --help` before showing the command, and include the Skill tool and the subagent tool (Agent or Task, whichever exists), in addition to the `clipper` command and the project folder. Never claim the permissions are exact or tested.
