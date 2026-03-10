import os
import re
from pathlib import Path


INDEXABLE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
}

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
}


def infer_file_role(path_text):
    name = Path(path_text).name.lower()

    if name in {"readme.md", "readme.txt"}:
        return "project documentation"
    if "tool" in name:
        return "tool implementations"
    if "assistant" in name:
        return "agent logic"
    if "executor" in name:
        return "execution loop"
    if "planner" in name or "plan" in name:
        return "planning logic"
    if "config" in name:
        return "configuration"
    if "cli" in name:
        return "command interface"
    if "memory" in name:
        return "memory or stored state"
    if "test" in name:
        return "tests"
    return "source file"


def build_workspace_map(workspace_root):
    root = Path(workspace_root).resolve()
    entries = []

    if not root.exists():
        return entries

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]

        for filename in filenames:
            path = Path(dirpath) / filename
            suffix = path.suffix.lower()
            if suffix not in INDEXABLE_EXTENSIONS:
                continue

            rel_path = path.relative_to(root).as_posix()
            entries.append(
                {
                    "path": rel_path,
                    "type": INDEXABLE_EXTENSIONS[suffix],
                    "role": infer_file_role(rel_path),
                }
            )

    entries.sort(key=lambda item: item["path"])
    return entries


def _tokenize(text):
    return [token for token in re.findall(r"[a-z0-9_]+", str(text or "").lower()) if len(token) >= 2]


def select_relevant_workspace_entries(workspace_map, query="", limit=20):
    entries = workspace_map if isinstance(workspace_map, list) else []
    if not entries:
        return []

    tokens = _tokenize(query)
    scored = []
    for index, entry in enumerate(entries):
        path_text = str(entry.get("path", "")).lower()
        role_text = str(entry.get("role", "")).lower()
        type_text = str(entry.get("type", "")).lower()

        score = 0
        for token in tokens:
            if token in path_text:
                score += 4
            if token in role_text:
                score += 3
            if token == type_text:
                score += 2

        if path_text.startswith("assistant_core/"):
            score += 1
        if path_text.endswith("/assistant.py") or path_text.endswith("/tools.py") or path_text.endswith("/config.py"):
            score += 1

        scored.append((score, index, entry))

    scored.sort(key=lambda item: (-item[0], item[1], item[2].get("path", "")))
    if tokens:
        top = [entry for score, _, entry in scored if score > 0][: max(1, int(limit))]
        if top:
            return top

    return entries[: max(1, int(limit))]


def format_workspace_overview(workspace_map, query="", limit=20):
    selected = select_relevant_workspace_entries(workspace_map, query=query, limit=limit)
    if not selected:
        return "Workspace overview unavailable."

    lines = []
    for entry in selected:
        lines.append(f"{entry['path']} - {entry['role']} ({entry['type']})")
    return "\n".join(lines)
