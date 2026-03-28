# AI Assistant

Local Ollama-powered AI assistant with memory, planning, tool use, and desktop automation.

## Verification Status

Last checked: 2026-03-24

Tested and working in this environment:

- CLI entrypoint and module imports
- Task creation
- Plan creation
- File listing in `workspace/`
- File reading in `workspace/`
- File creation in `workspace/`
- File append in `workspace/`
- File edit/overwrite in `workspace/`
- Workspace search
- Restricted `git status` command execution
- Disallowed command blocking
- Screenshot capture
- `open_app("notepad")`
- Python code execution correctly blocked for safety

Tested but not fully working in this environment:

- File delete: still blocked by `Access is denied` from the current environment or filesystem permissions; code now returns a clearer error and attempts trash fallback when available
- Web search: failed here with `WinError 10061`, so network access or local connectivity needs to be verified outside this session

Still needs manual end-to-end testing:

- Interactive CLI conversation flow
- Memory persistence across restarts
- Long-term memory extraction from user profile details
- Plan listing and plan inspection
- Task listing and task completion
- Autonomous execution cycle
- `open_app("vscode")`
- `open_app("chrome")`
- Mouse click automation with confirmation
- Keyboard text input automation with confirmation
- Planner/executor tool routing on real prompts
- Agent trace output review during normal usage

## Features

- Interactive CLI chat loop started from `ai_with_tools.py`
- Optional Windows voice conversation mode with microphone input
- Ollama model support with `OLLAMA_MODEL` environment variable override
- Optional offline text-to-speech for final assistant replies using `pyttsx3`
- Short-term conversation memory saved across runs
- Long-term memory extraction for user details like name and projects
- Task management: add, list, and complete tasks
- Plan management: create multi-step plans, list plans, inspect plan status
- Autonomous execution cycle for pending plan steps
- Planner + executor architecture for tool-augmented responses
- Agent trace logging for planner steps, executed steps, context, and final answer

## Tooling Features

- Workspace file operations inside `workspace/`
- File listing
- File reading
- File creation and overwrite with confirmation when needed
- File append
- File deletion with confirmation
- Restricted terminal command execution
- Allowed `git` commands: `status`, `log`, `branch`, `diff`, `rev-parse`, `show`
- Allowed Python command: `python -m py_compile <files>`
- Allowed app launching: `vscode`, `chrome`, `notepad`
- Web search using DuckDuckGo instant-answer API
- Workspace search across local text/code files
- Screenshot capture using `mss` and `Pillow`
- Mouse click automation with confirmation
- Keyboard text input automation with confirmation
- Basic math calculation

## Safety and Limits

- File access is restricted to the local `workspace/` directory for file commands
- Sensitive desktop input actions require explicit confirmation
- Shell control operators are blocked in terminal commands
- Direct Python code execution is currently disabled for safety
- File delete behavior depends on local filesystem permissions and may be blocked by the current runtime environment

## Main Components

- `ai_with_tools.py` - entrypoint
- `assistant_core/cli.py` - interactive chat loop
- `assistant_core/assistant.py` - memory, planning, routing, tool orchestration
- `assistant_core/tools.py` - tool implementations
- `assistant_core/executor.py` - autonomous step execution
- `assistant_core/workspace_index.py` - workspace indexing and search support

## Testing

## Text To Speech

Install the optional dependency:

```bash
pip install pyttsx3
```

Then run the assistant normally:

```bash
python ai_with_tools.py
```

By default, the CLI will speak final assistant replies after printing them.

Set `ASSISTANT_TTS=0` to disable speech without uninstalling the package.

On Windows, the assistant also includes a built-in PowerShell/System.Speech fallback for spoken output if `pyttsx3` is unavailable.

## Voice Conversation Mode

Run the assistant in voice mode on Windows:

```bash
python ai_with_tools.py --mode voice
```

The voice loop:

- listens for one spoken request at a time
- sends the transcript through the existing assistant pipeline
- speaks the reply through the audio output
- listens for interrupt and exit keywords while the reply is being spoken
- falls back to typed input in the same loop if speech recognition is unavailable

Useful environment variables:

- `ASSISTANT_MODE=voice`
- `ASSISTANT_INTERRUPT_KEYWORDS=stop assistant,stop speaking`
- `ASSISTANT_EXIT_KEYWORDS=exit voice mode,goodbye assistant`
- `ASSISTANT_STT_CULTURE=en-US`
- `ASSISTANT_TEXT_FALLBACK=1`
- `ASSISTANT_TTS_VOICE=Microsoft Zira Desktop`

## Local Python Environment

This project now uses a local virtual environment in `.venv`.

Start the assistant without manually activating the environment:

```bash
start_assistant.bat
```

PowerShell variant:

```powershell
.\start_assistant.ps1
```

The launcher scripts use `.venv\Scripts\python.exe` directly, so they automatically run in the project environment.

Use the feature-by-feature checklist in [TEST_CHECKLIST.md](/D:/nakul/python_ai_scripts/TEST_CHECKLIST.md).
