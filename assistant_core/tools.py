import json
import os
import shlex
import shutil
import subprocess

TOOLS = {
    "list_files": {
        "description": "List files in the workspace directory.",
        "args": {},
        "required": [],
    },
    "read_file": {
        "description": "Read a file from workspace.",
        "args": {"path": "string"},
        "required": ["path"],
    },
    "write_file": {
        "description": "Write content to a file in workspace (overwrites when approved).",
        "args": {"path": "string", "content": "string"},
        "required": ["path", "content"],
    },
    "run_command": {
        "description": "Run an allowed terminal command.",
        "args": {"command": "string"},
        "required": ["command"],
    },
    "open_app": {
        "description": "Launch an allowed application.",
        "args": {"app": "string"},
        "required": ["app"],
    },
}


def tools_prompt_text() -> str:
    return json.dumps(TOOLS, indent=2, ensure_ascii=False)


def calculator(expression: str) -> str:
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


def run_python_code(code: str) -> str:
    _ = code
    return (
        "Python execution is disabled for safety. "
        "Use explicit tools: list/read/write file, run command, or open app."
    )


def run_terminal_command(command: str, timeout_seconds: int = 20) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return "No command provided."

    if any(token in cmd for token in ["&&", "||", "|", ";", ">", "<", "`", "$("]):
        return "Command blocked: shell control operators are not allowed."

    try:
        parts = shlex.split(cmd, posix=False)
    except Exception as e:
        return f"Invalid command format: {e}"

    if not parts:
        return "No command provided."

    exe = parts[0].lower()
    if exe == "git":
        allowed_git_subcommands = {"status", "log", "branch", "diff", "rev-parse", "show"}
        if len(parts) < 2 or parts[1].lower() not in allowed_git_subcommands:
            return "Command not allowed. Allowed git commands: status, log, branch, diff, rev-parse, show."
    elif exe == "python":
        if len(parts) < 4 or parts[1:3] != ["-m", "py_compile"]:
            return "Command not allowed. Allowed python command: python -m py_compile <files>."
    else:
        return "Command not allowed. Allowed executables: git, python."

    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
            timeout=max(1, int(timeout_seconds)),
            shell=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        output = stdout
        if stderr:
            output = f"{output}\n{stderr}".strip()
        if not output:
            output = "(no output)"
        return f"[exit {result.returncode}] {output}"
    except Exception as e:
        return f"Command execution error: {e}"


def open_app(app_name: str) -> str:
    name = (app_name or "").strip().lower()
    if not name:
        return "No app name provided."

    app_candidates = {
        "vscode": [
            "code",
            r"C:\Users\Nakul\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        ],
        "chrome": [
            "chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "notepad": ["notepad"],
    }
    if name not in app_candidates:
        return "App not allowed. Allowed apps: vscode, chrome, notepad."

    for candidate in app_candidates[name]:
        executable = candidate
        if os.path.sep not in candidate and "/" not in candidate:
            executable = shutil.which(candidate)
        is_builtin_command = executable in {"notepad", "code", "chrome"}
        is_real_path = bool(executable) and os.path.exists(executable)
        if is_real_path or is_builtin_command:
            try:
                subprocess.Popen([executable], shell=False)
                return f"Opened app: {name}"
            except Exception:
                continue

    return f"Could not find or open app: {name}"
