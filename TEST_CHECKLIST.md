# Feature Test Checklist

Last updated: 2026-03-24

Use this checklist to verify the assistant feature-by-feature. Status values:

- `PASS` = tested and working in this environment
- `FAIL` = tested and currently not working
- `TODO` = not tested yet
- `BLOCKED` = could not verify because the environment prevented the test

## Core Startup

- `PASS` Import `ai_with_tools.py` entrypoint
- `TODO` Start the full interactive CLI with `python ai_with_tools.py`
- `TODO` Confirm `exit` cleanly closes the loop
- `TODO` Confirm `OLLAMA_MODEL` override changes the displayed model name

## Conversation and Memory

- `TODO` Send a normal chat message and verify a text response
- `TODO` Mention your name with `I am <name>` and verify it is stored in `memory.json`
- `TODO` Mention a project with `I am building ...` and verify it is stored in `memory.json`
- `TODO` Restart the assistant and verify short-term and long-term memory behavior
- `TODO` Confirm agent trace output appears after each request

## Tasks

- `PASS` Add task through assistant API
- `TODO` Add task through natural language in CLI
- `TODO` List tasks in CLI
- `TODO` Complete a task by id in CLI
- `TODO` Complete a task by text match in CLI

## Plans

- `PASS` Create a plan through assistant API
- `TODO` Create a plan through natural language in CLI
- `TODO` List plans in CLI
- `TODO` Show a plan by id in CLI
- `TODO` Verify plan steps create linked tasks
- `TODO` Verify completing linked tasks updates plan step status

## Autonomous Execution

- `TODO` Run `autonomous status`
- `TODO` Run `run autonomous cycle`
- `PASS` Verify background runner starts, reports status, and accepts stop requests through assistant API
- `TODO` Verify CLI remains responsive while a confirmed plan runs in background
- `TODO` Verify `status` reports the active background step
- `TODO` Verify `stop` interrupts at the next safe checkpoint
- `TODO` Verify `[Progress] Step x/y: ...` prints before the autonomous action
- `TODO` Verify `[Progress] Done.` prints after the autonomous action
- `TODO` Verify `ASSISTANT_SPEAK_PROGRESS=1` speaks progress updates
- `TODO` Verify a proposed command requires confirmation
- `TODO` Verify completing a step updates plan status

## File Operations In `workspace/`

- `PASS` List files
- `PASS` Read file
- `PASS` Create file
- `PASS` Append file
- `PASS` Edit file
- `FAIL` Delete file in current environment

Delete note:
The code path was updated to return a clearer error and attempt a trash fallback, but this environment still reports `Access is denied` for delete or rename operations inside `workspace/`.

## Command Execution

- `PASS` Allowed `git status`
- `TODO` Allowed `git log`
- `TODO` Allowed `git branch`
- `TODO` Allowed `git diff`
- `TODO` Allowed `git rev-parse`
- `TODO` Allowed `git show`
- `TODO` Allowed `python -m py_compile <file>`
- `PASS` Block disallowed commands such as `dir`
- `TODO` Block shell control operators such as `&&` or `|`

## App Launching

- `PASS` `open_app("notepad")`
- `TODO` `open_app("vscode")`
- `TODO` `open_app("chrome")`
- `TODO` Verify unknown app names are rejected

## Web and Workspace Search

- `PASS` Workspace search
- `BLOCKED` Web search in this environment

Web search note:
The code executed, but the request failed with `WinError 10061`. Re-test in a normal network-enabled runtime.

## Screen and Desktop Automation

- `PASS` Screenshot capture
- `TODO` Click action prompts for confirmation
- `TODO` Confirmed click executes successfully
- `TODO` Type action prompts for confirmation
- `TODO` Confirmed type executes successfully
- `TODO` Rejected click/type cancels the pending action

## Safety Behavior

- `PASS` Direct Python execution is blocked for safety
- `PASS` Sensitive desktop actions require confirmation
- `TODO` Overwriting an existing file requires confirmation
- `TODO` Deleting a file requires confirmation

## Suggested Manual Test Commands

Run these one by one in the CLI:

```text
list tasks
add task finish README verification
list plans
create plan for test the assistant end to end
show plan 1
list files
read file test.txt
write file demo_check.txt: hello
open notepad
capture screen
click 100 100
type hello from assistant
autonomous status
run autonomous cycle
```
