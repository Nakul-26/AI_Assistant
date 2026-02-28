import json
import os
import re
from datetime import datetime
from pathlib import Path

import ollama

from .config import MAX_SHORT_TERM_MESSAGES, MEMORY_FILE, SYSTEM_PROMPT
from .executor import AutonomousExecutor
from .tools import calculator, open_app, run_python_code, run_terminal_command


class AssistantWithMemory:
    def __init__(self, model="mistral", memory_file=MEMORY_FILE):
        self.model = model
        self.memory_file = memory_file
        self.workspace_dir = (Path(os.getcwd()) / "workspace").resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.memory = self.load_memory()
        self.executor = AutonomousExecutor(self)

    def load_memory(self):
        if not os.path.exists(self.memory_file):
            return {"short_term": [], "long_term": {}, "tasks": [], "plans": [], "pending_action": None}
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return {
                    "short_term": data[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": {},
                    "tasks": [],
                    "plans": [],
                    "pending_action": None,
                }

            if isinstance(data, dict):
                short_term = data.get("short_term", [])
                long_term = data.get("long_term", {})
                tasks = data.get("tasks", [])
                plans = data.get("plans", [])
                pending_action = data.get("pending_action")
                if not isinstance(short_term, list):
                    short_term = []
                if not isinstance(long_term, dict):
                    long_term = {}
                if not isinstance(tasks, list):
                    tasks = []
                if not isinstance(plans, list):
                    plans = []
                if pending_action is not None and not isinstance(pending_action, dict):
                    pending_action = None
                return {
                    "short_term": short_term[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": long_term,
                    "tasks": tasks,
                    "plans": plans,
                    "pending_action": pending_action,
                }

            return {"short_term": [], "long_term": {}, "tasks": [], "plans": [], "pending_action": None}
        except Exception:
            return {"short_term": [], "long_term": {}, "tasks": [], "plans": [], "pending_action": None}

    def save_memory(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Memory Save Error] {e}")

    def add_to_short_term(self, role, content):
        self.memory["short_term"].append({"role": role, "content": content})
        if len(self.memory["short_term"]) > MAX_SHORT_TERM_MESSAGES:
            self.memory["short_term"] = self.memory["short_term"][-MAX_SHORT_TERM_MESSAGES:]

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

    def ask_ai(self):
        long_term_info = json.dumps(self.memory["long_term"], indent=2, ensure_ascii=False)
        open_tasks = [t for t in self.memory["tasks"] if t.get("status") != "completed"]
        tasks_info = json.dumps(open_tasks, indent=2, ensure_ascii=False)
        active_plans = [p for p in self.memory.get("plans", []) if p.get("status") != "completed"]
        plans_info = json.dumps(active_plans, indent=2, ensure_ascii=False)
        system_with_memory = (
            f"{SYSTEM_PROMPT}\n"
            f"Long-term user memory:\n{long_term_info}\n\n"
            f"Open tasks/goals:\n{tasks_info}\n\n"
            f"Active plans:\n{plans_info}"
        )
        messages = [{"role": "system", "content": system_with_memory}] + self.memory["short_term"]
        response = ollama.chat(model=self.model, messages=messages)
        return response["message"]["content"]

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
            result = run_terminal_command(command)
            plan_id = payload.get("plan_id")
            step_no = payload.get("step")
            if result.startswith("[exit 0]") and isinstance(plan_id, int) and isinstance(step_no, int):
                if self.mark_plan_step_completed(plan_id, step_no):
                    result += f"\nAutonomy: marked Plan #{plan_id} Step {step_no} as completed."
            self._clear_pending_action()
            return result
        self._clear_pending_action()
        return "Pending action was invalid and has been cleared."

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
                os.remove(path)
                return f"File deleted: {path}"

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
        self.extract_long_term_memory(user_message)
        self.add_to_short_term("user", user_message)
        self.sync_plan_step_statuses_from_tasks()

        pending = self.memory.get("pending_action")
        if isinstance(pending, dict):
            if self._is_confirmation(user_message):
                final_reply = self._execute_pending_action() or "No pending action to confirm."
                self.add_to_short_term("assistant", final_reply)
                self.save_memory()
                return final_reply
            if self._is_rejection(user_message):
                self._clear_pending_action()
                final_reply = "Cancelled pending action."
                self.add_to_short_term("assistant", final_reply)
                self.save_memory()
                return final_reply
            final_reply = "A confirmation is pending. Reply 'yes' to proceed or 'no' to cancel."
            self.add_to_short_term("assistant", final_reply)
            self.save_memory()
            return final_reply

        plan_cmd = self.extract_plan_command(user_message)
        autonomous_cmd = self.extract_autonomous_command(user_message)
        task_cmd = self.extract_task_command(user_message)
        file_cmd = self.extract_file_command(user_message)
        system_cmd = self.extract_system_command(user_message)
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
                final_reply = run_terminal_command(system_cmd.get("command", ""))
            elif system_cmd["action"] == "open_app":
                final_reply = open_app(system_cmd.get("app_name", ""))
            else:
                final_reply = "I could not process that system command."
        elif expression:
            result = calculator(expression)
            final_reply = f"Result: {result}"
        elif code:
            final_reply = run_python_code(code)
        else:
            final_reply = self.ask_ai()

        self.add_to_short_term("assistant", final_reply)
        self.save_memory()
        return final_reply
