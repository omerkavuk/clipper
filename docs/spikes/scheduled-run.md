# Spike: can a scheduled task run the media report unattended?

## Purpose

Setup wants to schedule the `run` skill so the report arrives on its own. Before we make that the default, we need to know that a Claude desktop scheduled task can do the whole job with nobody watching. This spike is run once by hand. Its result decides which scheduling path setup uses.

Nothing in this spike is created automatically. You create the one-off task yourself and delete it afterwards.

## Procedure

1. Make sure the example project exists (`examples/` or one made with the setup skill) and ask Claude to list your monitoring projects to check it appears. Note its name.
2. In the Claude desktop app, open Scheduled tasks and create a **one-off** task for **5 minutes from now**.
   - Prompt: `Use the clipper run skill for the project '<name>'`
   - Folder: the Clipper plugin folder.
3. Stay away from the task window. Do not click any permission prompt that appears: just note that it appeared. (If one does appear, the task is not unattended.)
4. When it has finished, open the task's run and the project folder, and tick what happened. Where to look: checks (b) and (c) are visible in the scheduled task's run transcript; (d) is in the project folder.

| Check | Passed |
|---|---|
| (a) Ran with no permission prompt | [ ] |
| (b) The haiku tagging subagent started | [ ] |
| (c) The `clipper` commands ran (collect, candidates, apply-tags, draft-input, render) | [ ] |
| (d) The report (HTML and Markdown) and the run note were produced | [ ] |

5. **Asleep.** Create a second one-off task for 5 minutes ahead, then put the computer to sleep until after that time. Wake it. Record whether the task ran at once, ran later, or was skipped, and whether it covered the full missed period.
6. **Off.** Create a third one-off task for 5 minutes ahead, shut the computer down until after that time, then start it. Record the same things.
7. Delete all test tasks.

## Result

(To be filled in by the user.)

- Date and app version:
- (a) no permission prompt:
- (b) haiku subagent:
- (c) `clipper` commands:
- (d) report and run note:
- Asleep (task 2):
- Off (task 3):
- Path chosen (desktop scheduled task or fallback), and why:

## Decision rule

- If all four checks pass: nothing more to do. Setup uses the Claude desktop scheduled task (weekly, Monday 08:07 local by default), after the user says yes.
- If any check fails: note it in the Result section and tell Claude "use the backup scheduling method". The setup skill tries the desktop task first, falls back, after the user's explicit yes, if creating or running it fails, and uses the fallback whenever the user asks for it.

## Fallback command template (UNVERIFIED)

This template is a starting point only. It stays unverified until a fallback run of it passes in this spike. Before using it, check the flags with `claude --help`, and confirm the tool names: the subagent tool is called Agent or Task depending on the version, and the Skill tool must also be allowed. Path rules for Windows `C:\` paths and for folders with spaces or apostrophes may need adjusting.

The prompt is the same run instruction. Permissions are limited to the `clipper` command and the project folder.

    claude -p "Use the clipper run skill for the project '<name>'" --allowedTools "Skill" "Agent" "Bash(python -m uv run --directory \"<plugin root>\" clipper:*)" "Read(<project folder>/**)" "Write(<project folder>/**)" "Edit(<project folder>/**)"

That is the bash form, for the `.sh` script. The Windows `.cmd` script uses the same `\"` around the plugin root inside the quoted `Bash(...)` argument: cmd passes the line through unchanged, and `claude` (Node) splits its Windows arguments the CommandLineToArgvW way, where `\"` is a literal quote inside a quoted argument (UNVERIFIED, like the rest of this template):

    claude -p "Use the clipper run skill for the project 'NAME'" --allowedTools Skill Agent "Bash(python -m uv run --directory \"<plugin root>\" clipper:*)" "Read(<project folder>/**)" "Write(<project folder>/**)" "Edit(<project folder>/**)"

The command is too long and has nested quotes for the scheduler, so it goes in a wrapper script in the project folder, and the scheduler points at the script.

Windows: `run-clipper.cmd` holds the `claude -p ...` line. Register:

    schtasks /Create /SC WEEKLY /D MON /ST 08:07 /TN "Clipper - <name>" /TR "\"<project folder>\run-clipper.cmd\""

macOS: `run-clipper.sh` (executable) holds the `claude -p ...` line. Minimal plist at `~/Library/LaunchAgents/com.clipper.<name>.plist`:

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0"><dict>
      <key>Label</key><string>com.clipper.<name></string>
      <key>ProgramArguments</key><array><string>/bin/sh</string><string><project folder>/run-clipper.sh</string></array>
      <key>StartCalendarInterval</key><dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>7</integer></dict>
    </dict></plist>

In the plist, XML-escape `&`, `<` and `>` in `<name>` and `<project folder>`, and build the Label from the project name with spaces replaced by hyphens.

Register with `launchctl bootstrap gui/$(id -u) <plist path>` (the legacy form is `launchctl load <plist path>`).
