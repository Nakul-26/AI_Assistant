import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

import ollama

from .config import (
    MAX_TOOL_RESULT_CHARS,
    MAX_SHORT_TERM_MESSAGES,
    MAX_TOOL_STEPS,
    MAX_TOOL_TRACES,
    MEMORY_FILE,
    SYSTEM_PROMPT,
    TOOL_TIMEOUT_SECONDS,
)
from .executor import AutonomousExecutor
from .tools import (
    SENSITIVE_TOOLS,
    TOOLS,
    calculator,
    capture_screen,
    click_screen,
    format_web_results,
    open_app,
    run_python_code,
    run_terminal_command,
    type_text,
    tools_prompt_text,
    web_search,
    workspace_search,
)
from .workspace_index import build_workspace_map, format_workspace_overview


class AssistantWithMemory:
    def __init__(self, model="mistral", memory_file=MEMORY_FILE):
        self.model = model
        self.memory_file = memory_file
        self.repo_root = Path(os.getcwd()).resolve()
        self.workspace_dir = (Path(os.getcwd()) / "workspace").resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_map = build_workspace_map(self.repo_root)
        self.memory = self.load_memory()
        self.executor = AutonomousExecutor(self)

    def load_memory(self):
        if not os.path.exists(self.memory_file):
            return {
                "short_term": [],
                "long_term": {},
                "tasks": [],
                "plans": [],
                "pending_action": None,
                "pending_plan": None,
                "tool_traces": [],
            }
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return {
                    "short_term": self._normalize_short_term_messages(data)[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": {},
                    "tasks": [],
                    "plans": [],
                    "pending_action": None,
                    "pending_plan": None,
                    "tool_traces": [],
                }

            if isinstance(data, dict):
                short_term = self._normalize_short_term_messages(data.get("short_term", []))
                long_term = data.get("long_term", {})
                tasks = data.get("tasks", [])
                plans = data.get("plans", [])
                pending_action = data.get("pending_action")
                pending_plan = data.get("pending_plan")
                tool_traces = data.get("tool_traces", [])
                if not isinstance(long_term, dict):
                    long_term = {}
                if not isinstance(tasks, list):
                    tasks = []
                if not isinstance(plans, list):
                    plans = []
                if pending_action is not None and not isinstance(pending_action, dict):
                    pending_action = None
                if pending_plan is not None and not isinstance(pending_plan, dict):
                    pending_plan = None
                if not isinstance(tool_traces, list):
                    tool_traces = []
                return {
                    "short_term": short_term[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": long_term,
                    "tasks": tasks,
                    "plans": plans,
                    "pending_action": pending_action,
                    "pending_plan": pending_plan,
                    "tool_traces": tool_traces[-MAX_TOOL_TRACES:],
                }

            return {
                "short_term": [],
                "long_term": {},
                "tasks": [],
                "plans": [],
                "pending_action": None,
                "pending_plan": None,
                "tool_traces": [],
            }
        except Exception:
            return {
                "short_term": [],
                "long_term": {},
                "tasks": [],
                "plans": [],
                "pending_action": None,
                "pending_plan": None,
                "tool_traces": [],
            }

    def save_memory(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Memory Save Error] {e}")

    def _stringify_message_content(self, content):
        if isinstance(content, str):
            return content
        if isinstance(content, (dict, list)):
            try:
                return json.dumps(content, ensure_ascii=False)
            except Exception:
                return str(content)
        return str(content or "")

    def _normalize_short_term_messages(self, messages):
        if not isinstance(messages, list):
            return []

        normalized = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if not role:
                continue
            normalized.append(
                {
                    "role": role,
                    "content": self._stringify_message_content(item.get("content", "")),
                }
            )
        return normalized

    def add_to_short_term(self, role, content):
        self.memory["short_term"].append({"role": role, "content": self._stringify_message_content(content)})
        if len(self.memory["short_term"]) > MAX_SHORT_TERM_MESSAGES:
            self.memory["short_term"] = self.memory["short_term"][-MAX_SHORT_TERM_MESSAGES:]

    def add_tool_trace(self, request, step, action_payload, result):
        self.memory.setdefault("tool_traces", [])
        self.memory["tool_traces"].append(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "request": str(request or "").strip(),
                "step": step,
                "action": action_payload.get("action"),
                "args": action_payload.get("args", {}),
                "result": str(result or ""),
            }
        )
        if len(self.memory["tool_traces"]) > MAX_TOOL_TRACES:
            self.memory["tool_traces"] = self.memory["tool_traces"][-MAX_TOOL_TRACES:]

    def _start_agent_trace(self, user_message):
        self._active_trace = {
            "user": str(user_message or "").strip(),
            "plan": [],
            "steps": [],
            "context": [],
            "final_response": "",
        }

    def _record_trace_plan(self, plan_steps):
        if not isinstance(getattr(self, "_active_trace", None), dict):
            return
        self._active_trace["plan"] = list(plan_steps or [])

    def _record_trace_step(self, step_no, description, tool_name="", args=None, result=""):
        if not isinstance(getattr(self, "_active_trace", None), dict):
            return
        self._active_trace["steps"].append(
            {
                "step": step_no,
                "description": str(description or "").strip(),
                "tool": str(tool_name or "").strip(),
                "args": args if isinstance(args, dict) else {},
                "result": str(result or "").strip(),
            }
        )

    def _record_trace_context(self, label, content):
        if not isinstance(getattr(self, "_active_trace", None), dict):
            return
        self._active_trace["context"].append(
            {
                "label": str(label or "").strip(),
                "content": str(content or "").strip(),
            }
        )

    def _summarize_debug_text(self, text, max_chars=240):
        value = str(text or "").strip().replace("\r", " ").replace("\n", " ")
        value = re.sub(r"\s+", " ", value)
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + "..."

    def _finalize_agent_trace(self, final_response):
        trace = getattr(self, "_active_trace", None)
        if not isinstance(trace, dict):
            return

        trace["final_response"] = str(final_response or "").strip()
        print("\n=== AGENT TRACE ===")
        print(f"User: {trace.get('user', '')}")

        print("\n[PLANNER]")
        plan_steps = trace.get("plan", [])
        if plan_steps:
            for step in plan_steps:
                step_no = step.get("step", "?")
                description = step.get("description", "")
                tool_name = step.get("tool", "final")
                args = step.get("args", {})
                print(f"{step_no}. {description} [{tool_name}] {json.dumps(args, ensure_ascii=False)}")
        else:
            print("(no plan)")

        print("\n[EXECUTOR]")
        executed_steps = trace.get("steps", [])
        if executed_steps:
            for step in executed_steps:
                step_no = step.get("step", "?")
                description = step.get("description", "")
                tool_name = step.get("tool", "final")
                args = json.dumps(step.get("args", {}), ensure_ascii=False)
                print(f"STEP {step_no}  {tool_name}  {description}  {args}")
        else:
            print("(no executed steps)")

        print("\n[CONTEXT]")
        contexts = trace.get("context", [])
        if contexts:
            for item in contexts:
                print(f"{item.get('label', 'context')}: {item.get('content', '')}")
        else:
            print("(no collected context)")

        print("\n[FINAL RESPONSE]")
        print(trace.get("final_response", ""))
        print("\n=== TRACE END ===")
        self._active_trace = None

    def extract_long_term_memory(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        name_match = re.search(r"\b(?:i am|i'm|my name is)\s+([a-zA-Z][a-zA-Z\s'-]{0,40})\b", msg, re.IGNORECASE)
        if name_match:
            candidate = name_match.group(1).strip()
            if 1 <= len(candidate.split()) <= 3:
                self.memory["long_term"]["name"] = " ".join(part.capitalize() for part in candidate.split())

        project_match = re.search(
            r"\b(?:i am|i'm)\s+(?:building|working on|creating)\s+(?:an?\s+)?(.+?)(?:[.!?]|$)",
            msg,
            re.IGNORECASE,
        )
        if project_match:
            project = project_match.group(1).strip()
            if project:
                self.memory["long_term"].setdefault("projects", [])
                if project not in self.memory["long_term"]["projects"]:
                    self.memory["long_term"]["projects"].append(project)

        if "for reference" in msg_lower and "yes" in msg_lower:
            self.memory["long_term"]["reference_memory_enabled"] = True

    def _memory_context_text(self):
        long_term_info = json.dumps(self.memory["long_term"], indent=2, ensure_ascii=False)
        open_tasks = [t for t in self.memory["tasks"] if t.get("status") != "completed"]
        tasks_info = json.dumps(open_tasks, indent=2, ensure_ascii=False)
        active_plans = [p for p in self.memory.get("plans", []) if p.get("status") != "completed"]
        plans_info = json.dumps(active_plans, indent=2, ensure_ascii=False)
        return (
            f"Long-term user memory:\n{long_term_info}\n\n"
            f"Open tasks/goals:\n{tasks_info}\n\n"
            f"Active plans:\n{plans_info}"
        )

    def ask_ai(self):
        system_with_memory = f"{SYSTEM_PROMPT}\n{self._memory_context_text()}"
        messages = [{"role": "system", "content": system_with_memory}] + self.memory["short_term"]
        response = self._chat_with_role_fallback(messages)
        return response["message"]["content"]

    def _validate_tool_action_payload(self, payload):
        if not isinstance(payload, dict):
            return None

        action = str(payload.get("action", "")).strip()
        args = payload.get("args", {})
        if not action:
            return None
        if "args" not in payload:
            args = {}
        if not isinstance(args, dict):
            return None

        schema = TOOLS.get(action)
        if not schema:
            return None

        for key in schema.get("required", []):
            if key not in args:
                return None
            value = args.get(key)
            expected_type = schema.get("args", {}).get(key)
            if expected_type == "string":
                if not isinstance(value, str) or not value.strip():
                    return None
            elif expected_type == "integer":
                if not isinstance(value, int):
                    return None

        return {"action": action, "args": args}

    def parse_json_tool_action(self, response_text):
        payload = self._extract_json_object(response_text)
        return self._validate_tool_action_payload(payload)

    def parse_model_response_envelope(self, response_text):
        payload = self._extract_json_object(response_text)
        if not isinstance(payload, dict):
            return None

        response_type = str(payload.get("type", "")).strip().lower()
        if response_type not in {"tool", "final"}:
            return None
        if "content" not in payload:
            return None

        content = payload.get("content")
        if response_type == "final":
            if isinstance(content, str):
                return {"type": "final", "content": content}
            return {"type": "final", "content": json.dumps(content, ensure_ascii=False)}

        action_payload = self._validate_tool_action_payload(content)
        if not action_payload:
            return None
        return {"type": "tool", "content": action_payload}

    def parse_execution_plan(self, response_text):
        payload = self._extract_json_object(response_text)
        if not isinstance(payload, dict):
            return None

        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list):
            return None

        plan_steps = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if isinstance(raw_step, dict):
                description = str(raw_step.get("description") or raw_step.get("step") or "").strip()
                tool_name = str(raw_step.get("tool", "")).strip()
                args = raw_step.get("args", {})
            else:
                description = str(raw_step).strip()
                tool_name = ""
                args = {}

            if not description:
                continue

            if tool_name and tool_name not in TOOLS and tool_name != "final":
                tool_name = ""
            if not isinstance(args, dict):
                args = {}

            plan_steps.append(
                {
                    "step": index,
                    "description": description,
                    "tool": tool_name,
                    "args": args,
                }
            )

        return plan_steps or None

    def execute_json_tool_action(self, action_payload):
        action = action_payload["action"]
        args = action_payload.get("args", {})

        if action == "list_files":
            return self.execute_file_command({"action": "list"})
        if action == "read_file":
            return self.execute_file_command({"action": "read", "path": args.get("path", "")})
        if action == "write_file":
            return self.execute_file_command(
                {
                    "action": "create",
                    "path": args.get("path", ""),
                    "content": str(args.get("content", "")),
                }
            )
        if action == "run_command":
            return run_terminal_command(str(args.get("command", "")), timeout_seconds=TOOL_TIMEOUT_SECONDS)
        if action == "open_app":
            return open_app(str(args.get("app", "")))
        if action == "web_search":
            raw_results = web_search(str(args.get("query", "")))
            return format_web_results(raw_results)
        if action == "workspace_search":
            return workspace_search(str(args.get("query", "")), workspace_root=str(self.workspace_dir))
        if action == "capture_screen":
            return capture_screen()
        if action == "click":
            if not bool(args.get("confirmed")):
                pending_payload = {"action": action, "args": {"x": args.get("x"), "y": args.get("y"), "confirmed": True}}
                self._set_pending_action("tool_action", pending_payload)
                return f"AI wants to click at ({args.get('x')}, {args.get('y')}). Reply 'yes' to confirm or 'no' to cancel."
            return click_screen(args.get("x"), args.get("y"))
        if action == "type_text":
            if not bool(args.get("confirmed")):
                pending_payload = {"action": action, "args": {"text": args.get("text", ""), "confirmed": True}}
                self._set_pending_action("tool_action", pending_payload)
                return (
                    f"AI wants to type {len(str(args.get('text', '')))} characters into the focused window. "
                    "Reply 'yes' to confirm or 'no' to cancel."
                )
            return type_text(str(args.get("text", "")))

        return f"Unsupported tool action: {action}"

    def infer_relevant_tools(self, user_message):
        text = str(user_message or "").lower()
        tools = set()

        if any(k in text for k in ["file", "workspace", "read", "write", "edit", "append", "delete", "list"]):
            tools.update(["list_files", "read_file", "write_file"])

        if "git" in text or "command" in text or "terminal" in text or "shell" in text:
            tools.add("run_command")

        if any(k in text for k in ["open", "launch", "start", "app"]):
            tools.add("open_app")
        if any(
            k in text
            for k in [
                "capture screen",
                "screen capture",
                "screenshot",
                "screen",
                "desktop",
                "display",
            ]
        ):
            tools.add("capture_screen")
        if any(
            k in text
            for k in [
                "search",
                "web",
                "internet",
                "latest",
                "current",
                "today",
                "news",
                "docs",
                "documentation",
                "what is",
                "how to",
                "error",
                "fix",
                "stack overflow",
            ]
        ):
            tools.add("web_search")
        if any(
            k in text
            for k in [
                "project",
                "codebase",
                "code",
                "workspace",
                "repository",
                "repo",
                "find in",
                "where is",
                "which file",
                "explain this project",
                "search workspace",
            ]
        ):
            tools.add("workspace_search")

        # Keep hints restricted to tools exposed in TOOLS.
        return [name for name in TOOLS.keys() if name in tools and name not in SENSITIVE_TOOLS]

    def _planner_debug_enabled(self):
        value = os.getenv("ASSISTANT_DEBUG_PLANNER", "0").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _should_use_direct_chat(self, user_message, hinted_tools=None):
        msg = str(user_message or "").strip()
        if not msg:
            return True

        if hinted_tools:
            return False

        msg_lower = msg.lower()
        word_count = len(re.findall(r"\b[\w']+\b", msg_lower))

        if word_count <= 3:
            return True

        short_conversational_patterns = [
            r"^(hi|hello|hey|heya|yo)$",
            r"^(thanks|thank you)$",
            r"^(yes|yeah|yep|no|nope|okay|ok)$",
            r"^(good morning|good afternoon|good evening|good night)$",
            r"^(i am|i'm|my name is)\s+[a-zA-Z][a-zA-Z\s'-]{0,40}$",
        ]
        if any(re.fullmatch(pattern, msg_lower) for pattern in short_conversational_patterns):
            return True

        if word_count <= 8 and any(token in msg_lower for token in ["hello", "hi", "hey", "thanks", "thank you", "i am", "i'm", "my name is"]):
            return True

        return False

    def generate_execution_plan(self, user_message="", hinted_tools=None):
        tool_list_text = tools_prompt_text(selected_tools=hinted_tools) if hinted_tools else tools_prompt_text()
        workspace_overview = format_workspace_overview(self.workspace_map, query=user_message, limit=20)
        planner_prompt = (
            f"{SYSTEM_PROMPT}\n"
            "You are the planner for a local AI assistant.\n"
            "Break the user request into a short execution plan before any tool call happens.\n"
            "Choose the best tool for each step when a tool is helpful.\n"
            "Use only these tool names when needed: "
            f"{', '.join(name for name in TOOLS.keys() if name not in SENSITIVE_TOOLS)}.\n"
            "Use tool \"final\" for a step that should answer directly without a tool.\n"
            "Keep the plan minimal: 1 to 4 steps.\n"
            "Return ONLY valid JSON with this schema:\n"
            '{"steps":[{"step":1,"description":"...","tool":"tool_name|final","args":{"path":"optional","query":"optional"}}]}\n'
            "Example:\n"
            '{"steps":['
            '{"step":1,"description":"Inspect assistant_core/assistant.py","tool":"read_file","args":{"path":"assistant_core/assistant.py"}},'
            '{"step":2,"description":"Inspect assistant_core/tools.py","tool":"read_file","args":{"path":"assistant_core/tools.py"}},'
            '{"step":3,"description":"Summarize the architecture","tool":"final","args":{}}'
            "]}\n"
            "No markdown. No commentary.\n\n"
            "Workspace overview:\n"
            f"{workspace_overview}\n\n"
            "Available tools:\n"
            f"{tool_list_text}\n\n"
            f"{self._memory_context_text()}\n\n"
            f"User request: {user_message}"
        )
        response_text = ""
        try:
            response = self._chat_with_role_fallback([{"role": "system", "content": planner_prompt}])
            response_text = response["message"]["content"]
            parsed = self.parse_execution_plan(response_text)
            if parsed:
                return parsed
            if self._planner_debug_enabled():
                print("\n[PLANNER RAW RESPONSE SUMMARY]")
                print(self._summarize_debug_text(response_text))
                print("\n[PLANNER PARSE RESULT]")
                print(None)
        except Exception:
            if self._planner_debug_enabled():
                print("\n[PLANNER RAW RESPONSE SUMMARY]")
                print(self._summarize_debug_text(response_text or "(planner call failed before content was returned)"))
                print("\n[PLANNER PARSE RESULT]")
                print(None)

        heuristic_plan = self._heuristic_execution_plan(user_message, hinted_tools=hinted_tools)
        if self._planner_debug_enabled():
            print("\n[PLANNER FALLBACK USED]")
            print(heuristic_plan)
        self._record_trace_plan(heuristic_plan)
        return heuristic_plan

    def generate_plan(self, user_message="", hinted_tools=None):
        return self.generate_execution_plan(user_message=user_message, hinted_tools=hinted_tools)

    def _heuristic_execution_plan(self, user_message="", hinted_tools=None):
        msg = str(user_message or "").strip()
        msg_lower = msg.lower()

        assistant_path = self._workspace_map_path("assistant.py")
        tools_path = self._workspace_map_path("tools.py")
        config_path = self._workspace_map_path("config.py")

        if "explain" in msg_lower and any(token in msg_lower for token in ["project", "repo", "codebase"]):
            steps = []
            if assistant_path:
                steps.append(self._plan_step("Inspect the main agent logic", "read_file", path=assistant_path))
            if tools_path:
                steps.append(self._plan_step("Inspect the tool implementations", "read_file", path=tools_path))
            steps.append(self._plan_step("Summarize the project architecture", "final"))
            return self._normalize_plan_steps(steps)

        if "tool hint" in msg_lower or "tool selection" in msg_lower:
            steps = []
            if assistant_path:
                steps.append(self._plan_step("Inspect tool hinting and tool selection logic", "read_file", path=assistant_path))
            if tools_path:
                steps.append(self._plan_step("Inspect tool definitions", "read_file", path=tools_path))
            steps.append(self._plan_step("Explain which file controls tool selection", "final"))
            return self._normalize_plan_steps(steps)

        if "which file" in msg_lower and "tool" in msg_lower:
            steps = []
            if assistant_path:
                steps.append(self._plan_step("Inspect assistant_core/assistant.py for dispatch and hinting", "read_file", path=assistant_path))
            if tools_path:
                steps.append(self._plan_step("Inspect tool definitions in tools.py", "read_file", path=tools_path))
            steps.append(self._plan_step("Name the controlling file and relevant function", "final"))
            return self._normalize_plan_steps(steps)

        if "py_compile" in msg_lower:
            steps = []
            if config_path:
                steps.append(self._plan_step("Inspect runtime configuration", "read_file", path=config_path))
            if assistant_path:
                steps.append(self._plan_step("Inspect assistant execution paths that reference py_compile", "read_file", path=assistant_path))
            steps.append(self._plan_step("Search the workspace for py_compile references", "workspace_search", query="py_compile"))
            steps.append(self._plan_step("Explain the likely cause of the py_compile failure", "final"))
            return self._normalize_plan_steps(steps)

        if "find where" in msg_lower or "where is" in msg_lower or "where" in msg_lower:
            if "tool" in msg_lower:
                steps = [
                    self._plan_step("Search the workspace for tool-related logic", "workspace_search", query=msg),
                ]
                if assistant_path:
                    steps.append(self._plan_step("Inspect assistant_core/assistant.py", "read_file", path=assistant_path))
                steps.append(self._plan_step("Explain where the relevant logic lives", "final"))
                return self._normalize_plan_steps(steps)

        fallback_tool = "final"
        fallback_args = {}
        if hinted_tools:
            fallback_tool = hinted_tools[0]
            if fallback_tool == "workspace_search":
                fallback_args = {"query": msg}
        return self._normalize_plan_steps([self._plan_step(msg or "Respond to the user.", fallback_tool, **fallback_args)])

    def _workspace_map_path(self, filename):
        target = str(filename or "").strip().lower()
        exact_matches = []
        suffix_matches = []
        for entry in self.workspace_map:
            path_text = str(entry.get("path", ""))
            path_lower = path_text.lower()
            if Path(path_text).name.lower() == target:
                exact_matches.append(path_text)
            elif path_lower.endswith("/" + target):
                suffix_matches.append(path_text)
        prioritized = sorted(exact_matches, key=lambda value: (0 if value.startswith("assistant_core/") else 1, value))
        if prioritized:
            return prioritized[0]
        prioritized = sorted(suffix_matches, key=lambda value: (0 if value.startswith("assistant_core/") else 1, value))
        if prioritized:
            return prioritized[0]
        return ""

    def _plan_step(self, description, tool, **args):
        clean_args = {key: value for key, value in args.items() if isinstance(value, str) and value.strip()}
        return {
            "description": str(description or "").strip(),
            "tool": str(tool or "").strip(),
            "args": clean_args,
        }

    def _normalize_plan_steps(self, steps):
        normalized = []
        for index, step in enumerate(steps, start=1):
            description = str(step.get("description", "")).strip()
            tool = str(step.get("tool", "")).strip() or "final"
            args = step.get("args", {})
            if not description:
                continue
            if (tool not in TOOLS or tool in SENSITIVE_TOOLS) and tool != "final":
                tool = "final"
            if not isinstance(args, dict):
                args = {}
            normalized.append(
                {
                    "step": index,
                    "description": description,
                    "tool": tool,
                    "args": args,
                }
            )
        return normalized or [{"step": 1, "description": "Respond to the user.", "tool": "final", "args": {}}]

    def ask_ai_with_json_tools(self, user_message=""):
        hinted_tools = self.infer_relevant_tools(user_message)
        if self._should_use_direct_chat(user_message, hinted_tools=hinted_tools):
            return self.ask_ai()
        execution_plan = self.generate_plan(user_message=user_message, hinted_tools=hinted_tools)
        self._set_pending_plan(
            "tool_execution",
            {
                "user_message": user_message,
                "hinted_tools": sorted(hinted_tools) if hinted_tools else [],
                "execution_plan": execution_plan,
            },
        )
        return self._format_plan_preview(execution_plan)

    def _run_json_tool_plan(self, user_message, execution_plan, hinted_tools=None):
        hinted_tools = hinted_tools or []
        self._record_trace_plan(execution_plan)
        tool_list_text = tools_prompt_text(selected_tools=hinted_tools) if hinted_tools else tools_prompt_text()
        plan_text = json.dumps(execution_plan, indent=2, ensure_ascii=False)
        tool_prompt = (
            f"{SYSTEM_PROMPT}\n"
            "You are the executor for a local AI assistant.\n"
            "A planner has already created the execution plan. Execute one planned step at a time.\n"
            "When responding, you MUST return ONLY valid JSON in this exact shape:\n"
            '{"type":"tool|final","content":...}\n'
            "If a tool is needed, return:\n"
            '{"type":"tool","content":{"action":"<tool_name>","args":{...}}}\n'
            "If no tool is needed, return:\n"
            '{"type":"final","content":"<final answer>"}\n'
            "No markdown, no code fences, and no text outside the JSON.\n"
            "After tool execution, you will receive a message with role `tool_result` containing raw tool output.\n"
            "Use prior completed step results when handling later steps.\n"
            "Prefer the tool selected by the planner for the current step.\n"
            "Do not hallucinate tool results.\n\n"
            "Relevant tools for this request:\n"
            f"{tool_list_text}\n\n"
            "Planner output:\n"
            f"{plan_text}\n\n"
            f"{self._memory_context_text()}"
        )
        messages = [{"role": "system", "content": tool_prompt}] + list(self.memory["short_term"])
        completed_steps = []

        for plan_step in execution_plan[:MAX_TOOL_STEPS]:
            step_index = plan_step.get("step")
            step_description = plan_step.get("description", "")
            expected_tool = plan_step.get("tool", "")
            expected_args = plan_step.get("args", {})
            step_prompt = (
                f"Execute planned step {step_index}: {step_description}\n"
                f"Preferred tool: {expected_tool or 'final'}\n"
                f"Suggested args: {json.dumps(expected_args, ensure_ascii=False)}\n"
                f"Completed step results so far:\n{json.dumps(completed_steps, indent=2, ensure_ascii=False)}"
            )
            messages.append({"role": "user", "content": step_prompt})
            response = self._chat_with_role_fallback(messages)
            content = response["message"]["content"]
            messages.append({"role": "assistant", "content": content})

            envelope = self.parse_model_response_envelope(content)
            if not envelope:
                return content

            if envelope["type"] == "final":
                self._record_trace_step(step_index, step_description, "final", expected_args, envelope["content"])
                return envelope["content"]

            action_payload = envelope["content"]
            if "action" not in action_payload:
                return "Invalid tool call from model."
            if action_payload["action"] in SENSITIVE_TOOLS:
                return f"Sensitive tool '{action_payload['action']}' is manual-only and not available to the planner."
            if hinted_tools and action_payload["action"] not in hinted_tools:
                print(f"[tool-warning] Model chose '{action_payload['action']}' outside hint set {hinted_tools}")

            tool_result = self.execute_json_tool_action(action_payload)
            self.add_tool_trace(user_message, step_index, action_payload, tool_result)
            summarized_result = self.summarize_tool_result(action_payload, tool_result)
            self._record_trace_step(
                step_index,
                step_description,
                action_payload.get("action"),
                action_payload.get("args", {}),
                summarized_result,
            )
            if action_payload.get("action") == "read_file":
                self._record_trace_context(action_payload.get("args", {}).get("path", "read_file"), summarized_result)
            elif action_payload.get("action") == "workspace_search":
                self._record_trace_context("workspace_search", summarized_result)
            elif action_payload.get("action") == "capture_screen":
                self._record_trace_context("capture_screen", summarized_result)
            if isinstance(self.memory.get("pending_action"), dict) or "Reply 'yes'" in tool_result:
                return tool_result

            messages.append({"role": "tool_result", "content": summarized_result})
            completed_steps.append(
                {
                    "step": step_index,
                    "description": step_description,
                    "tool": action_payload.get("action"),
                    "result": summarized_result,
                }
            )

        final_response = self._chat_with_role_fallback(
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        "The planned steps are complete. Return a final response now using ONLY this JSON envelope: "
                        '{"type":"final","content":"..."} '
                        "Do not call any more tools."
                    ),
                }
            ]
        )
        final_content = final_response["message"]["content"]
        final_envelope = self.parse_model_response_envelope(final_content)
        if final_envelope and final_envelope["type"] == "final":
            return final_envelope["content"]
        return final_content

    def _chat_with_role_fallback(self, messages):
        try:
            return ollama.chat(model=self.model, messages=messages)
        except Exception:
            normalized = []
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "tool_result":
                    normalized.append({"role": "user", "content": f"TOOL_RESULT:\n{content}"})
                else:
                    normalized.append(msg)
            return ollama.chat(
                model=self.model,
                messages=normalized,
            )

    def summarize_tool_result(self, action_payload, result):
        if isinstance(result, (dict, list)):
            text = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            text = str(result or "")
        max_chars = MAX_TOOL_RESULT_CHARS
        action = str((action_payload or {}).get("action", "")).strip().lower()

        # Keep high-signal slices for verbose command output.
        if action == "run_command":
            lines = text.splitlines()
            if len(lines) > 120:
                head = lines[:80]
                tail = lines[-20:]
                text = "\n".join(head + ["...[omitted lines]..."] + tail)
        elif action == "capture_screen":
            try:
                payload = result if isinstance(result, dict) else json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                image_path = str(payload.get("image_path", "")).strip()
                width = payload.get("width")
                height = payload.get("height")
                timestamp = payload.get("timestamp")
                details = []
                if image_path:
                    details.append(f"image_path: {image_path}")
                if width and height:
                    details.append(f"resolution: {width}x{height}")
                if timestamp:
                    details.append(f"timestamp: {timestamp}")
                if details:
                    text = "\n".join(details)
        elif action == "click":
            try:
                payload = result if isinstance(result, dict) else json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                text = f"clicked: ({payload.get('x', '?')}, {payload.get('y', '?')})"
        elif action == "type_text":
            try:
                payload = result if isinstance(result, dict) else json.loads(text)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                text = f"typed_text_length: {payload.get('text_length', '?')}"

        if len(text) <= max_chars:
            return text

        return text[:max_chars] + "\n...[truncated]..."

    def _next_task_id(self):
        max_id = 0
        for task in self.memory["tasks"]:
            if isinstance(task.get("id"), int):
                max_id = max(max_id, task["id"])
        return max_id + 1

    def _next_plan_id(self):
        max_id = 0
        for plan in self.memory.get("plans", []):
            if isinstance(plan.get("id"), int):
                max_id = max(max_id, plan["id"])
        return max_id + 1

    def add_task(self, title):
        title = title.strip().rstrip(".")
        if not title:
            return None

        task = {
            "id": self._next_task_id(),
            "title": title,
            "status": "in_progress",
            "created_at": datetime.utcnow().date().isoformat(),
        }
        self.memory["tasks"].append(task)
        return task

    def list_tasks_text(self):
        if not self.memory["tasks"]:
            return "You currently have no tasks."

        lines = []
        for task in self.memory["tasks"]:
            task_id = task.get("id", "?")
            title = task.get("title", "Untitled task")
            status = task.get("status", "unknown")
            created_at = task.get("created_at", "unknown-date")
            lines.append(f"{task_id}. [{status}] {title} (created: {created_at})")
        return "Your tasks:\n" + "\n".join(lines)

    def complete_task(self, query):
        query = query.strip().lower()
        if not query:
            return None

        if query.isdigit():
            task_id = int(query)
            for task in self.memory["tasks"]:
                if task.get("id") == task_id:
                    task["status"] = "completed"
                    self.sync_plan_step_statuses_from_tasks()
                    return task

        for task in self.memory["tasks"]:
            title = str(task.get("title", "")).lower()
            if query in title:
                task["status"] = "completed"
                self.sync_plan_step_statuses_from_tasks()
                return task

        return None

    def sync_plan_step_statuses_from_tasks(self):
        tasks_by_id = {task.get("id"): task for task in self.memory["tasks"] if isinstance(task.get("id"), int)}
        for plan in self.memory.get("plans", []):
            all_done = True
            any_pending = False
            for step in plan.get("steps", []):
                task_id = step.get("task_id")
                if isinstance(task_id, int) and task_id in tasks_by_id:
                    task_status = tasks_by_id[task_id].get("status", "in_progress")
                    step["status"] = "completed" if task_status == "completed" else "pending"
                if step.get("status") != "completed":
                    all_done = False
                    any_pending = True
            if plan.get("steps"):
                if all_done:
                    plan["status"] = "completed"
                elif any_pending:
                    plan["status"] = "in_progress"

    def _extract_json_object(self, text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
        return None

    def generate_plan_steps(self, goal):
        planner_system = (
            "You are a planning assistant. "
            "Break the user's goal into 3-8 concrete implementation steps. "
            "Return ONLY valid JSON with this schema: "
            '{"steps":[{"step":1,"description":"..."}]}. '
            "No markdown."
        )
        planner_user = f"Goal: {goal}"
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": planner_system},
                    {"role": "user", "content": planner_user},
                ],
            )
            content = response["message"]["content"]
            payload = self._extract_json_object(content)
            if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
                clean_steps = []
                for i, step in enumerate(payload["steps"], start=1):
                    if isinstance(step, dict):
                        desc = str(step.get("description", "")).strip()
                    else:
                        desc = str(step).strip()
                    if desc:
                        clean_steps.append({"step": i, "description": desc, "status": "pending"})
                if clean_steps:
                    return clean_steps
        except Exception:
            pass

        return [
            {"step": 1, "description": f"Define scope and requirements for: {goal}", "status": "pending"},
            {"step": 2, "description": "Break work into implementation modules", "status": "pending"},
            {"step": 3, "description": "Implement core functionality first", "status": "pending"},
            {"step": 4, "description": "Test, refine, and document the solution", "status": "pending"},
        ]

    def create_plan(self, goal, auto_add_tasks=True):
        goal = goal.strip().rstrip(".")
        if not goal:
            return None

        steps = self.generate_plan_steps(goal)
        plan = {
            "id": self._next_plan_id(),
            "goal": goal,
            "status": "in_progress",
            "created_at": datetime.utcnow().date().isoformat(),
            "steps": steps,
        }

        if auto_add_tasks:
            for step in plan["steps"]:
                task_title = f"[Plan #{plan['id']} Step {step['step']}] {step['description']}"
                task = self.add_task(task_title)
                if task:
                    step["task_id"] = task["id"]

        self.memory.setdefault("plans", []).append(plan)
        return plan

    def format_plan_text(self, plan):
        lines = [
            f"Plan #{plan.get('id', '?')} - Goal: {plan.get('goal', 'Untitled goal')}",
            f"Status: {plan.get('status', 'unknown')}",
            "Steps:",
        ]
        for step in plan.get("steps", []):
            step_no = step.get("step", "?")
            desc = step.get("description", "")
            status = step.get("status", "pending")
            task_id = step.get("task_id")
            if isinstance(task_id, int):
                lines.append(f"{step_no}. [{status}] {desc} (task #{task_id})")
            else:
                lines.append(f"{step_no}. [{status}] {desc}")
        return "\n".join(lines)

    def list_plans_text(self):
        plans = self.memory.get("plans", [])
        if not plans:
            return "You currently have no plans."
        lines = []
        for plan in plans:
            plan_id = plan.get("id", "?")
            goal = plan.get("goal", "Untitled goal")
            status = plan.get("status", "unknown")
            steps_count = len(plan.get("steps", []))
            lines.append(f"{plan_id}. [{status}] {goal} ({steps_count} steps)")
        return "Your plans:\n" + "\n".join(lines)

    def get_plan_by_id(self, plan_id):
        for plan in self.memory.get("plans", []):
            if plan.get("id") == plan_id:
                return plan
        return None

    def mark_plan_step_completed(self, plan_id, step_no):
        plan = self.get_plan_by_id(plan_id)
        if not plan:
            return False

        for step in plan.get("steps", []):
            if step.get("step") == step_no:
                step["status"] = "completed"
                task_id = step.get("task_id")
                if isinstance(task_id, int):
                    self.complete_task(str(task_id))
                else:
                    self.sync_plan_step_statuses_from_tasks()
                return True
        return False

    def extract_plan_command(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        if any(phrase in msg_lower for phrase in ["list plans", "show plans", "my plans", "current plans"]):
            return {"action": "list"}

        show_match = re.search(r"(?:show|view)\s+plan\s+(\d+)", msg_lower, re.IGNORECASE)
        if show_match:
            return {"action": "show", "plan_id": int(show_match.group(1))}

        plan_patterns = [
            r"(?:create|make|build|generate)\s+(?:a\s+)?plan\s+(?:for|to)\s+(.+)",
            r"(?:plan|break\s+down)\s*[:\-]?\s*(.+)",
        ]
        for pattern in plan_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                goal = match.group(1).strip()
                if goal:
                    return {"action": "create", "goal": goal}

        return None

    def extract_autonomous_command(self, user_message):
        msg_lower = user_message.strip().lower()

        if msg_lower in {"autonomous status", "status autonomous", "autonomy status"}:
            return {"action": "status"}

        if re.search(r"\brun\s+autonomous(?:\s+cycle)?\b", msg_lower) or msg_lower in {"autonomous", "run autonomy"}:
            return {"action": "run_cycle"}

        return None

    def extract_task_command(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        if any(
            phrase in msg_lower
            for phrase in [
                "list tasks",
                "show tasks",
                "my tasks",
                "current tasks",
                "what are my tasks",
                "what are my current tasks",
            ]
        ):
            return {"action": "list"}

        add_patterns = [
            r"(?:add|create)\s+(?:a\s+)?task\s*[:\-]?\s*(.+)",
            r"remember\s+that\s+i\s+want\s+to\s+(.+)",
            r"goal\s*[:\-]?\s*(.+)",
        ]
        for pattern in add_patterns:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                title = match.group(1).strip()
                if title:
                    return {"action": "add", "title": title}

        complete_match = re.search(
            r"(?:mark|set)\s+(?:task\s+)?(.+?)\s+(?:as\s+)?(?:complete|completed|done)",
            msg,
            re.IGNORECASE,
        )
        if complete_match:
            return {"action": "complete", "query": complete_match.group(1).strip()}

        done_match = re.search(r"(?:complete|finish|done)\s+task\s+(.+)", msg, re.IGNORECASE)
        if done_match:
            return {"action": "complete", "query": done_match.group(1).strip()}

        return None

    def extract_math_expression(self, user_message):
        msg = user_message.lower()

        if re.fullmatch(r"[\d\.\+\-\*/%\(\)\s]+", msg) and re.search(r"[\+\-\*/%]", msg):
            return msg.strip()

        match = re.search(r"(?:calculate|calc|solve)\s*[:\-]?\s*([\d\.\+\-\*/%\(\)\s]+)", msg)
        if match:
            expr = match.group(1).strip()
            if re.fullmatch(r"[\d\.\+\-\*/%\(\)\s]+", expr) and re.search(r"[\+\-\*/%]", expr):
                return expr

        return None

    def extract_python_code(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        has_exec_intent = any(w in msg_lower for w in ["run", "execute", "test", "show output"])
        if not has_exec_intent:
            return None

        block = re.search(r"```(?:python)?\s*([\s\S]*?)```", msg, re.IGNORECASE)
        if block:
            return block.group(1).strip()

        inline = re.search(r"(?:run|execute|test)\s+python\s*[:\-]?\s*([\s\S]+)", msg, re.IGNORECASE)
        if inline:
            return inline.group(1).strip()

        return None

    def extract_system_command(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        command_match = re.search(r"^(?:run|execute)\s+command\s+(.+)$", msg, re.IGNORECASE)
        if command_match:
            return {"action": "command", "command": command_match.group(1).strip()}

        command_colon_match = re.search(r"^command\s*:\s*(.+)$", msg, re.IGNORECASE)
        if command_colon_match:
            return {"action": "command", "command": command_colon_match.group(1).strip()}

        app_match = re.search(r"^(?:open|launch)\s+app\s+([a-zA-Z0-9_\-\. ]+)$", msg, re.IGNORECASE)
        if app_match:
            return {"action": "open_app", "app_name": app_match.group(1).strip()}

        app_short_match = re.search(r"^(?:open|launch)\s+([a-zA-Z0-9_\-\. ]+)$", msg, re.IGNORECASE)
        if app_short_match and msg_lower.split()[0] in {"open", "launch"}:
            return {"action": "open_app", "app_name": app_short_match.group(1).strip()}

        return None

    def extract_screen_command(self, user_message):
        msg = str(user_message or "").strip().lower()
        if not msg:
            return None

        exact_phrases = {
            "capture screen",
            "capture the screen",
            "capture my screen",
            "take screenshot",
            "take a screenshot",
            "take screen shot",
            "take a screen shot",
            "screenshot",
            "screen capture",
        }
        if msg in exact_phrases:
            return {"action": "capture_screen"}
        return None

    def extract_input_command(self, user_message):
        msg = str(user_message or "").strip()
        if not msg:
            return None

        click_match = re.match(r"^\s*click\s+(-?\d+)\s+(-?\d+)\s*$", msg, re.IGNORECASE)
        if click_match:
            return {
                "action": "click",
                "args": {"x": int(click_match.group(1)), "y": int(click_match.group(2))},
                "description": f"Click at screen coordinate ({int(click_match.group(1))}, {int(click_match.group(2))})",
            }

        type_match = re.match(r"^\s*type(?:\s+text)?\s*:\s*([\s\S]+)$", msg, re.IGNORECASE)
        if type_match:
            return {
                "action": "type_text",
                "args": {"text": type_match.group(1)},
                "description": "Type text into the focused application",
            }

        type_match = re.match(r"^\s*type(?:\s+text)?\s+([\s\S]+)$", msg, re.IGNORECASE)
        if type_match:
            return {
                "action": "type_text",
                "args": {"text": type_match.group(1)},
                "description": "Type text into the focused application",
            }

        return None

    def _clean_path_token(self, path_text):
        path = (path_text or "").strip().strip("'\"`")
        # Allow natural-language separators like "file - C:\\a\\b.txt" or "file: C:\\a\\b.txt".
        path = re.sub(r"^\s*[-:]+\s*", "", path)
        path = path.rstrip(".,!?")
        if path in {"\\", "/", ".\\", "./"}:
            return "."
        return path

    def _resolve_workspace_path(self, user_path):
        token = self._clean_path_token(user_path)
        if not token or token == ".":
            return None, "Please provide a filename relative to workspace."

        candidate = Path(token)
        if candidate.is_absolute() or re.match(r"^[a-zA-Z]:[\\/]", token):
            return None, f"Use a path relative to workspace: {self.workspace_dir}"

        resolved = (self.workspace_dir / candidate).resolve()
        workspace_text = str(self.workspace_dir)
        resolved_text = str(resolved)
        if not (resolved_text == workspace_text or resolved_text.startswith(workspace_text + os.sep)):
            return None, "Invalid path. Access is limited to the workspace directory."
        return resolved, None

    def extract_file_command(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        if "file" not in msg_lower and "files" not in msg_lower:
            return None

        if re.match(r"^\s*list\s+files\b", msg_lower):
            return {"action": "list"}

        if re.match(r"^\s*read\s+file\b", msg_lower):
            read_target = re.sub(r"^\s*read\s+file\b", "", msg, flags=re.IGNORECASE).strip()
            if read_target:
                return {"action": "read", "path": self._clean_path_token(read_target)}

        if re.match(r"^\s*write\s+file\b", msg_lower):
            parts = msg.split(":", 1)
            if len(parts) < 2:
                return {"action": "write_help"}
            file_part = re.sub(r"^\s*write\s+file\b", "", parts[0], flags=re.IGNORECASE).strip()
            return {
                "action": "create",
                "path": self._clean_path_token(file_part),
                "content": self._normalize_requested_content(parts[1].strip()),
            }

        create_intent = any(word in msg_lower for word in ["create", "make", "new"])
        edit_intent = any(word in msg_lower for word in ["edit", "update", "overwrite", "replace"])
        append_intent = any(word in msg_lower for word in ["append", "add to"])
        delete_intent = any(word in msg_lower for word in ["delete", "remove"])
        read_intent = any(word in msg_lower for word in ["read", "show", "display"])

        content_match = re.search(r"(?:with\s+content|containing)\s+([\s\S]+)$", msg, re.IGNORECASE)
        content = content_match.group(1).strip() if content_match else ""

        combined_update_match = re.search(
            r"(?:read|show|display)\s+(?:the\s+)?file\s+(.+?)\s+and\s+"
            r"(?:update|edit|overwrite|replace)\s+(?:its\s+)?contents?\s+(?:to|with)\s+([\s\S]+)$",
            msg,
            re.IGNORECASE,
        )
        if combined_update_match:
            return {
                "action": "edit",
                "path": self._clean_path_token(combined_update_match.group(1)),
                "content": self._normalize_requested_content(combined_update_match.group(2).strip()),
            }

        if create_intent:
            name_match = re.search(r"(?:called|named)\s+([^\n]+?)(?:\s+with\s+content|\s+containing|$)", msg, re.IGNORECASE)
            dir_match = re.search(r"(?:at|in)\s+([^\s]+)", msg, re.IGNORECASE)

            if name_match:
                filename = self._clean_path_token(name_match.group(1))
                directory = self._clean_path_token(dir_match.group(1)) if dir_match else "."
                return {"action": "create", "path": os.path.join(directory, filename), "content": content}

            direct_match = re.search(
                r"(?:create|make|new)\s+(?:a\s+)?file\s+(?:at|in)?\s*([^\s]+)",
                msg,
                re.IGNORECASE,
            )
            if direct_match:
                return {"action": "create", "path": self._clean_path_token(direct_match.group(1)), "content": content}

        if edit_intent:
            edit_match = re.search(
                r"(?:edit|update|overwrite|replace)\s+(?:the\s+)?file\s+(.+?)(?:\s+with|\s+to)\s+([\s\S]+)$",
                msg,
                re.IGNORECASE,
            )
            if edit_match:
                return {
                    "action": "edit",
                    "path": self._clean_path_token(edit_match.group(1)),
                    "content": self._normalize_requested_content(edit_match.group(2).strip()),
                }

        if append_intent:
            append_match = re.search(
                r"(?:append|add)\s+(?:to\s+)?(?:the\s+)?file\s+(.+?)(?:\s*:\s*|\s+)([\s\S]+)$",
                msg,
                re.IGNORECASE,
            )
            if append_match:
                return {
                    "action": "append",
                    "path": self._clean_path_token(append_match.group(1)),
                    "content": self._normalize_requested_content(append_match.group(2).strip()),
                }

        if delete_intent:
            delete_match = re.search(r"(?:delete|remove)\s+(?:the\s+)?file\s+(.+)$", msg, re.IGNORECASE)
            if delete_match:
                return {"action": "delete", "path": self._clean_path_token(delete_match.group(1))}

        if read_intent:
            read_match = re.search(r"(?:read|show|display)\s+(?:the\s+)?file\s+(.+?)(?:\s+and\s+|$)", msg, re.IGNORECASE)
            if read_match:
                return {"action": "read", "path": self._clean_path_token(read_match.group(1))}

        return None

    def _normalize_requested_content(self, content):
        text = (content or "").strip()
        python_print_match = re.search(r"python\s+code\s+that\s+prints\s+(.+)$", text, re.IGNORECASE)
        if python_print_match:
            value = python_print_match.group(1).strip().strip("'\"")
            return f'print("{value}")'
        return text

    def _is_confirmation(self, message):
        msg = (message or "").strip().lower()
        return msg in {"yes", "y", "confirm", "confirmed", "proceed", "do it", "ok", "okay"}

    def _is_rejection(self, message):
        msg = (message or "").strip().lower()
        return msg in {"no", "n", "cancel", "stop", "dont", "don't"}

    def _set_pending_action(self, action_type, payload):
        self.memory["pending_action"] = {"type": action_type, "payload": payload}

    def _clear_pending_action(self):
        self.memory["pending_action"] = None

    def _set_pending_plan(self, plan_type, payload):
        self.memory["pending_plan"] = {"type": plan_type, "payload": payload}

    def _clear_pending_plan(self):
        self.memory["pending_plan"] = None

    def _execute_pending_action(self):
        pending = self.memory.get("pending_action")
        if not isinstance(pending, dict):
            return None
        action_type = pending.get("type")
        payload = pending.get("payload")
        if action_type == "file" and isinstance(payload, dict):
            result = self.execute_file_command(payload, approved=True)
            self._clear_pending_action()
            return result
        if action_type == "system_command" and isinstance(payload, dict):
            command = str(payload.get("command", "")).strip()
            result = run_terminal_command(command, timeout_seconds=TOOL_TIMEOUT_SECONDS)
            plan_id = payload.get("plan_id")
            step_no = payload.get("step")
            if result.startswith("[exit 0]") and isinstance(plan_id, int) and isinstance(step_no, int):
                if self.mark_plan_step_completed(plan_id, step_no):
                    result += f"\nAutonomy: marked Plan #{plan_id} Step {step_no} as completed."
            self._clear_pending_action()
            return result
        if action_type == "tool_action" and isinstance(payload, dict):
            result = self.execute_json_tool_action(payload)
            self._clear_pending_action()
            return result
        self._clear_pending_action()
        return "Pending action was invalid and has been cleared."

    def _execute_pending_plan(self):
        pending = self.memory.get("pending_plan")
        if not isinstance(pending, dict):
            return None
        plan_type = pending.get("type")
        payload = pending.get("payload")
        self._clear_pending_plan()

        if plan_type == "tool_execution" and isinstance(payload, dict):
            return self._run_json_tool_plan(
                payload.get("user_message", ""),
                payload.get("execution_plan", []),
                hinted_tools=payload.get("hinted_tools"),
            )

        return "Pending plan was invalid and has been cleared."

    def _format_plan_preview(self, execution_plan):
        lines = []
        for step in execution_plan[:MAX_TOOL_STEPS]:
            step_no = step.get("step", len(lines) + 1)
            description = str(step.get("description", "")).strip() or "Perform the next action."
            lines.append(f"{step_no}. {description}")
        plan_text = "\n".join(lines) if lines else "1. Respond to the user."
        return f"I plan to:\n{plan_text}\n\nProceed? (yes/no)"

    def execute_file_command(self, command, approved=False):
        action = command.get("action")
        if action == "list":
            items = sorted([p.name for p in self.workspace_dir.iterdir()]) if self.workspace_dir.exists() else []
            if not items:
                return f"Workspace is empty: {self.workspace_dir}"
            return f"Workspace files ({self.workspace_dir}):\n" + "\n".join(items)

        if action == "write_help":
            return "Use format: write file <relative_path>: <content>"

        raw_path = self._clean_path_token(command.get("path", ""))
        if not raw_path:
            return "I could not determine the file path."

        path, path_error = self._resolve_workspace_path(raw_path)
        if path_error:
            return path_error

        try:
            if not approved:
                if action == "delete":
                    self._set_pending_action("file", command)
                    return f"Confirm delete of {path}? Reply 'yes' to proceed or 'no' to cancel."
                if action in {"create", "edit"} and os.path.exists(path):
                    self._set_pending_action("file", command)
                    return f"File exists and will be overwritten: {path}. Reply 'yes' to confirm or 'no' to cancel."

            if action in {"create", "edit", "append"}:
                directory = os.path.dirname(str(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)

            if action == "create":
                content = command.get("content", "")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"File created: {path} (workspace)"

            if action == "edit":
                content = command.get("content", "")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                return f"File updated: {path} (workspace)"

            if action == "append":
                content = command.get("content", "")
                with open(path, "a", encoding="utf-8") as f:
                    f.write(content)
                return f"Appended to file: {path} (workspace)"

            if action == "delete":
                if not os.path.exists(path):
                    return f"File not found: {path}"
                if not os.path.isfile(path):
                    return f"Path is not a file: {path}"
                try:
                    os.chmod(path, stat.S_IWRITE)
                except Exception:
                    pass

                try:
                    os.remove(path)
                    return f"File deleted: {path}"
                except PermissionError:
                    try:
                        from send2trash import send2trash

                        send2trash(str(path))
                        return f"File moved to trash: {path}"
                    except Exception:
                        return (
                            f"File delete failed: access denied for {path}. "
                            "This usually means the file is locked by another process or the current environment "
                            "does not allow delete/rename operations in this directory."
                        )

            if action == "read":
                if not os.path.exists(path):
                    return f"File not found: {path}"
                if not os.path.isfile(path):
                    return f"Path is not a file: {path}"
                with open(path, "r", encoding="utf-8") as f:
                    data = f.read()
                if not data:
                    return f"File is empty: {path}"
                return f"--- File: {path} ---\n{data}"

            return "I could not process that file command."
        except Exception as e:
            return f"File operation error: {e}"

    def process_request(self, user_message):
        self._start_agent_trace(user_message)
        self.extract_long_term_memory(user_message)
        self.add_to_short_term("user", user_message)
        self.sync_plan_step_statuses_from_tasks()

        pending = self.memory.get("pending_action")
        if isinstance(pending, dict):
            if self._is_confirmation(user_message):
                final_reply = self._execute_pending_action() or "No pending action to confirm."
                if pending.get("type") == "tool_action" and isinstance(pending.get("payload"), dict):
                    action_payload = pending.get("payload")
                    action_name = action_payload.get("action", "tool_action")
                    action_args = action_payload.get("args", {})
                    if isinstance(action_args, dict) and "confirmed" in action_args:
                        action_args = {k: v for k, v in action_args.items() if k != "confirmed"}
                    summary = self.summarize_tool_result(action_payload, final_reply)
                    description = "Execute confirmed input action"
                    if action_name == "click":
                        description = f"Click at screen coordinate ({action_args.get('x', '?')}, {action_args.get('y', '?')})"
                    elif action_name == "type_text":
                        description = "Type text into the focused application"
                    self.add_tool_trace("confirmed pending action", 1, action_payload, final_reply)
                    self._record_trace_plan([{"step": 1, "description": description, "tool": action_name, "args": action_args}])
                    self._record_trace_step(1, description, action_name, action_args, summary)
                    self._record_trace_context(action_name, summary)
                self.add_to_short_term("assistant", final_reply)
                self._finalize_agent_trace(final_reply)
                self.save_memory()
                return final_reply
            if self._is_rejection(user_message):
                self._clear_pending_action()
                final_reply = "Cancelled pending action."
                self.add_to_short_term("assistant", final_reply)
                self._finalize_agent_trace(final_reply)
                self.save_memory()
                return final_reply
            final_reply = "A confirmation is pending. Reply 'yes' to proceed or 'no' to cancel."
            self.add_to_short_term("assistant", final_reply)
            self._finalize_agent_trace(final_reply)
            self.save_memory()
            return final_reply

        pending_plan = self.memory.get("pending_plan")
        if isinstance(pending_plan, dict):
            if self._is_confirmation(user_message):
                final_reply = self._execute_pending_plan() or "No pending plan to confirm."
                self.add_to_short_term("assistant", final_reply)
                self._finalize_agent_trace(final_reply)
                self.save_memory()
                return final_reply
            if self._is_rejection(user_message):
                self._clear_pending_plan()
                final_reply = "Okay, cancelled."
                self.add_to_short_term("assistant", final_reply)
                self._finalize_agent_trace(final_reply)
                self.save_memory()
                return final_reply
            self._clear_pending_plan()

        plan_cmd = self.extract_plan_command(user_message)
        autonomous_cmd = self.extract_autonomous_command(user_message)
        task_cmd = self.extract_task_command(user_message)
        file_cmd = self.extract_file_command(user_message)
        system_cmd = self.extract_system_command(user_message)
        screen_cmd = self.extract_screen_command(user_message)
        input_cmd = self.extract_input_command(user_message)
        expression = self.extract_math_expression(user_message)
        code = self.extract_python_code(user_message)

        if autonomous_cmd:
            action = autonomous_cmd["action"]
            if action == "run_cycle":
                final_reply = self.executor.run_cycle()
            elif action == "status":
                final_reply = self.executor.status_text()
            else:
                final_reply = "I could not process that autonomous command."
        elif plan_cmd:
            action = plan_cmd["action"]
            if action == "create":
                plan = self.create_plan(plan_cmd["goal"], auto_add_tasks=True)
                if plan:
                    final_reply = self.format_plan_text(plan) + "\n\nTasks added to your task list."
                else:
                    final_reply = "I could not create a plan. Please provide a goal."
            elif action == "list":
                final_reply = self.list_plans_text()
            elif action == "show":
                plan = self.get_plan_by_id(plan_cmd["plan_id"])
                if plan:
                    final_reply = self.format_plan_text(plan)
                else:
                    final_reply = "I could not find that plan."
            else:
                final_reply = "I could not process that plan command."
        elif task_cmd:
            action = task_cmd["action"]
            if action == "add":
                task = self.add_task(task_cmd["title"])
                if task:
                    final_reply = f"Task added: #{task['id']} - {task['title']}"
                else:
                    final_reply = "I could not add that task. Please provide a task title."
            elif action == "list":
                final_reply = self.list_tasks_text()
            elif action == "complete":
                task = self.complete_task(task_cmd["query"])
                if task:
                    final_reply = f"Marked complete: #{task['id']} - {task['title']}"
                else:
                    final_reply = "I could not find that task to mark complete."
            else:
                final_reply = "I could not process that task command."
        elif file_cmd:
            final_reply = self.execute_file_command(file_cmd)
        elif system_cmd:
            if system_cmd["action"] == "command":
                final_reply = run_terminal_command(system_cmd.get("command", ""), timeout_seconds=TOOL_TIMEOUT_SECONDS)
            elif system_cmd["action"] == "open_app":
                final_reply = open_app(system_cmd.get("app_name", ""))
            else:
                final_reply = "I could not process that system command."
        elif screen_cmd:
            action_payload = {"action": "capture_screen", "args": {}}
            tool_result = self.execute_json_tool_action(action_payload)
            self.add_tool_trace(user_message, 1, action_payload, tool_result)
            summary = self.summarize_tool_result(action_payload, tool_result)
            self._record_trace_plan([{"step": 1, "description": "Capture the current screen", "tool": "capture_screen", "args": {}}])
            self._record_trace_step(1, "Capture the current screen", "capture_screen", {}, summary)
            self._record_trace_context("capture_screen", summary)
            if isinstance(tool_result, dict):
                final_reply = (
                    f"Captured screen to {tool_result.get('image_path', 'screenshots')} "
                    f"({tool_result.get('width', '?')}x{tool_result.get('height', '?')})."
                )
            else:
                final_reply = str(tool_result)
        elif input_cmd:
            action_payload = {"action": input_cmd["action"], "args": input_cmd.get("args", {})}
            tool_result = self.execute_json_tool_action(action_payload)
            self.add_tool_trace(user_message, 1, action_payload, tool_result)
            summary = self.summarize_tool_result(action_payload, tool_result)
            self._record_trace_plan([{"step": 1, "description": input_cmd.get("description", input_cmd["action"]), "tool": input_cmd["action"], "args": input_cmd.get("args", {})}])
            self._record_trace_step(1, input_cmd.get("description", input_cmd["action"]), input_cmd["action"], input_cmd.get("args", {}), summary)
            self._record_trace_context(input_cmd["action"], summary)
            final_reply = str(tool_result)
        elif expression:
            result = calculator(expression)
            final_reply = f"Result: {result}"
        elif code:
            final_reply = run_python_code(code)
        else:
            final_reply = self.ask_ai_with_json_tools(user_message=user_message)

        self.add_to_short_term("assistant", final_reply)
        self._finalize_agent_trace(final_reply)
        self.save_memory()
        return final_reply
