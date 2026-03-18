import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse, urlunparse

SENSITIVE_TOOLS = {"click", "type_text"}

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
    "web_search": {
        "description": "Search the web for external information.",
        "args": {"query": "string"},
        "required": ["query"],
    },
    "workspace_search": {
        "description": "Search local workspace files for relevant code/text snippets.",
        "args": {"query": "string"},
        "required": ["query"],
    },
    "capture_screen": {
        "description": "Capture the current desktop screen and save an image.",
        "args": {},
        "required": [],
    },
    "click": {
        "description": "Click at a specific screen coordinate.",
        "args": {"x": "integer", "y": "integer"},
        "required": ["x", "y"],
    },
    "type_text": {
        "description": "Type text into the currently focused application.",
        "args": {"text": "string"},
        "required": ["text"],
    },
}


def tools_prompt_text(selected_tools=None, include_sensitive=False) -> str:
    available_tools = TOOLS
    if not include_sensitive:
        available_tools = {name: schema for name, schema in TOOLS.items() if name not in SENSITIVE_TOOLS}

    if not selected_tools:
        return json.dumps(available_tools, indent=2, ensure_ascii=False)

    selected = set(selected_tools)
    filtered = {name: schema for name, schema in available_tools.items() if name in selected}
    if not filtered:
        filtered = available_tools
    return json.dumps(filtered, indent=2, ensure_ascii=False)


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


def capture_screen(output_dir: str = "screenshots"):
    try:
        import mss
    except Exception as e:
        return f"Screen capture unavailable: mss is not installed ({e})."

    try:
        from PIL import Image
    except Exception as e:
        return f"Screen capture unavailable: Pillow is not installed ({e})."

    target_dir = os.path.abspath(output_dir or "screenshots")
    os.makedirs(target_dir, exist_ok=True)

    ts = int(time.time())
    image_path = os.path.join(target_dir, f"screen_{ts}.png")

    try:
        with mss.mss() as sct:
            monitors = getattr(sct, "monitors", [])
            if len(monitors) < 2:
                return "Screen capture unavailable: no primary monitor detected."

            monitor = monitors[1]
            shot = sct.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            image.save(image_path)
    except Exception as e:
        return f"Screen capture error: {e}"

    return {
        "image_path": os.path.relpath(image_path, os.getcwd()).replace("\\", "/"),
        "width": int(shot.size[0]),
        "height": int(shot.size[1]),
        "timestamp": ts,
    }


def click_screen(x, y):
    try:
        import pyautogui
    except Exception as e:
        return f"Click unavailable: pyautogui is not installed ({e})."

    try:
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        x_pos = int(x)
        y_pos = int(y)
    except Exception as e:
        return f"Click unavailable: invalid coordinates ({e})."

    try:
        pyautogui.click(x=x_pos, y=y_pos)
    except pyautogui.FailSafeException:
        return "Click aborted by failsafe. Move the mouse away from the screen corner and try again."
    except Exception as e:
        return f"Click error: {e}"

    return {"action": "click", "x": x_pos, "y": y_pos, "status": "clicked"}


def type_text(text):
    try:
        import pyautogui
    except Exception as e:
        return f"Type unavailable: pyautogui is not installed ({e})."

    content = str(text or "")
    if not content:
        return "Type unavailable: no text provided."

    try:
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05
        pyautogui.write(content, interval=0.01)
    except pyautogui.FailSafeException:
        return "Typing aborted by failsafe. Move the mouse away from the screen corner and try again."
    except Exception as e:
        return f"Type error: {e}"

    return {"action": "type_text", "text_length": len(content), "status": "typed"}


def web_search(query: str, max_results: int = 5, timeout_seconds: int = 10) -> str:
    q = (query or "").strip()
    if not q:
        return "No search query provided."

    endpoint = "https://api.duckduckgo.com/"
    params = urllib.parse.urlencode(
        {
            "q": q,
            "format": "json",
            "no_redirect": 1,
            "no_html": 1,
            "skip_disambig": 0,
        }
    )
    url = f"{endpoint}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=max(1, int(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
    except urllib.error.URLError as e:
        return f"Web search error: {e}"
    except Exception as e:
        return f"Web search error: {e}"

    results = []

    abstract_text = str(payload.get("AbstractText", "")).strip()
    abstract_url = str(payload.get("AbstractURL", "")).strip()
    heading = str(payload.get("Heading", "")).strip() or "DuckDuckGo instant answer"
    if abstract_text:
        results.append({"title": heading, "url": abstract_url, "snippet": abstract_text})

    for item in payload.get("Results", []):
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            continue
        text = str(item.get("Text", "")).strip()
        link = str(item.get("FirstURL", "")).strip()
        if text:
            results.append({"title": text[:100], "url": link, "snippet": text})

    for item in payload.get("RelatedTopics", []):
        if len(results) >= max_results:
            break
        if not isinstance(item, dict):
            continue

        nested = item.get("Topics")
        if isinstance(nested, list):
            for child in nested:
                if len(results) >= max_results:
                    break
                if not isinstance(child, dict):
                    continue
                text = str(child.get("Text", "")).strip()
                link = str(child.get("FirstURL", "")).strip()
                if text:
                    results.append({"title": text[:100], "url": link, "snippet": text})
            continue

        text = str(item.get("Text", "")).strip()
        link = str(item.get("FirstURL", "")).strip()
        if text:
            results.append({"title": text[:100], "url": link, "snippet": text})

    if not results:
        return f'No web results found for "{q}".'

    return json.dumps({"query": q, "results": results[:max_results]}, indent=2, ensure_ascii=False)


def normalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        cleaned = parsed._replace(query="", fragment="", netloc=parsed.netloc.lower())
        return urlunparse(cleaned)
    except Exception:
        return text


def dedupe_results(results):
    if not isinstance(results, list):
        return []

    seen = set()
    unique = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "") or item.get("href", "")).strip()
        norm = normalize_url(url)
        if norm:
            if norm in seen:
                continue
            seen.add(norm)
        unique.append(item)
    return unique


def format_web_results(raw_results, max_items: int = 5) -> str:
    payload = raw_results

    if isinstance(raw_results, str):
        try:
            payload = json.loads(raw_results)
        except Exception:
            return raw_results

    if not isinstance(payload, dict):
        return str(raw_results)

    query = str(payload.get("query", "")).strip()
    items = payload.get("results", [])
    if not isinstance(items, list):
        return str(raw_results)
    items = dedupe_results(items)

    lines = [
        "WEB_SEARCH_RESULTS",
        f"Query: {query}",
        "",
        "Sources may contain outdated or incorrect information. Use them as references, not facts.",
        "",
    ]

    for i, item in enumerate(items[:max(1, int(max_items))], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip() or "Untitled result"
        link = str(item.get("url", "") or item.get("href", "")).strip()
        snippet = str(item.get("snippet", "") or item.get("body", "") or item.get("text", "")).strip()

        lines.append(f"[{i}] {title}")
        if link:
            lines.append(link)
        if snippet:
            lines.append(snippet)
        lines.append("")

    return "\n".join(lines).strip()


def _tokenize_query(text: str):
    return [t for t in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(t) >= 2]


def _is_probably_text_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    text_exts = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".m",
        ".mm",
        ".scala",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".md",
        ".txt",
        ".html",
        ".css",
        ".sql",
        ".xml",
    }
    return ext in text_exts


def _best_snippet(content: str, query_tokens):
    lines = (content or "").splitlines()
    if not lines:
        return ""
    if not query_tokens:
        return lines[0].strip()[:220]

    for line in lines:
        line_l = line.lower()
        if any(tok in line_l for tok in query_tokens):
            return line.strip()[:220]
    return lines[0].strip()[:220]


def workspace_search(query: str, workspace_root: str = "", max_results: int = 5) -> str:
    q = str(query or "").strip()
    if not q:
        return "No workspace query provided."

    root = str(workspace_root or "").strip()
    if not root:
        root = os.path.join(os.getcwd(), "workspace")

    if not os.path.isdir(root):
        return f"Workspace directory not found: {root}"

    query_tokens = _tokenize_query(q)
    if not query_tokens:
        return "Workspace query did not contain searchable keywords."

    skip_dirs = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache"}
    max_file_bytes = 512 * 1024
    matches = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]

        for name in filenames:
            path = os.path.join(dirpath, name)
            if not _is_probably_text_file(path):
                continue

            try:
                if os.path.getsize(path) > max_file_bytes:
                    continue
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue

            content_l = content.lower()
            token_hits = 0
            frequency = 0
            for token in query_tokens:
                if token in content_l:
                    token_hits += 1
                    frequency += content_l.count(token)

            if token_hits == 0:
                continue

            score = (token_hits / max(1, len(query_tokens))) + min(0.5, frequency / 50.0)
            rel_path = os.path.relpath(path, root).replace("\\", "/")
            snippet = _best_snippet(content, query_tokens)
            matches.append({"path": rel_path, "score": score, "snippet": snippet})

    if not matches:
        return f'No workspace matches found for "{q}".'

    matches.sort(key=lambda m: m["score"], reverse=True)
    top = matches[: max(1, int(max_results))]

    lines = ["WORKSPACE_MATCHES", f"Query: {q}", ""]
    for i, item in enumerate(top, start=1):
        lines.append(f"[{i}] {item['path']}")
        lines.append(f"Score: {item['score']:.2f}")
        if item["snippet"]:
            lines.append(f"Snippet: {item['snippet']}")
        lines.append("")

    return "\n".join(lines).strip()
