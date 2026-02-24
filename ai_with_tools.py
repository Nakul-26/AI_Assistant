import ollama
import io
import sys
import json
import os
import re
from datetime import datetime

# =======================
# STEP 1 - TOOLS
# =======================

def calculator(expression: str) -> str:
    try:
        result = eval(expression)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


def run_python_code(code: str) -> str:
    old_stdout = sys.stdout
    try:
        # Remove markdown formatting if present
        code = code.replace("```python", "").replace("```", "").strip()

        # Capture printed output
        sys.stdout = buffer = io.StringIO()

        local_vars = {}
        exec(code, {}, local_vars)

        output = buffer.getvalue()
        if output.strip():
            return output
        return str(local_vars)
    except Exception as e:
        return f"Error: {e}"
    finally:
        sys.stdout = old_stdout


# =======================
# STEP 2 - SYSTEM PROMPT
# =======================

SYSTEM_PROMPT = """
You are a helpful AI assistant.
Answer clearly and directly.
Use prior conversation context when relevant.
"""


# =======================
# STEP 3 - MEMORY + ASSISTANT
# =======================

MEMORY_FILE = "memory.json"
MAX_SHORT_TERM_MESSAGES = 20


class AssistantWithMemory:
    def __init__(self, model="mistral", memory_file=MEMORY_FILE):
        self.model = model
        self.memory_file = memory_file
        self.memory = self.load_memory()

    def load_memory(self):
        if not os.path.exists(self.memory_file):
            return {"short_term": [], "long_term": {}, "tasks": [], "plans": []}
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Backward compatibility: migrate old list-based memory into short_term.
            if isinstance(data, list):
                return {
                    "short_term": data[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": {},
                    "tasks": [],
                    "plans": [],
                }

            if isinstance(data, dict):
                short_term = data.get("short_term", [])
                long_term = data.get("long_term", {})
                tasks = data.get("tasks", [])
                plans = data.get("plans", [])
                if not isinstance(short_term, list):
                    short_term = []
                if not isinstance(long_term, dict):
                    long_term = {}
                if not isinstance(tasks, list):
                    tasks = []
                if not isinstance(plans, list):
                    plans = []
                return {
                    "short_term": short_term[-MAX_SHORT_TERM_MESSAGES:],
                    "long_term": long_term,
                    "tasks": tasks,
                    "plans": plans,
                }

            return {"short_term": [], "long_term": {}, "tasks": [], "plans": []}
        except Exception:
            return {"short_term": [], "long_term": {}, "tasks": [], "plans": []}

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

        # Name extraction: "I am Nakul" / "I'm Nakul" / "My name is Nakul"
        name_match = re.search(r"\b(?:i am|i'm|my name is)\s+([a-zA-Z][a-zA-Z\s'-]{0,40})\b", msg, re.IGNORECASE)
        if name_match:
            candidate = name_match.group(1).strip()
            # Keep short names and title-case for readability.
            if 1 <= len(candidate.split()) <= 3:
                self.memory["long_term"]["name"] = " ".join(part.capitalize() for part in candidate.split())

        # Lightweight project extraction for phrases like:
        # "I am building an AI assistant"
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

        # Optional user-consent-style marker for "for reference yes".
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

        # Try exact id match first.
        if query.isdigit():
            task_id = int(query)
            for task in self.memory["tasks"]:
                if task.get("id") == task_id:
                    task["status"] = "completed"
                    self.sync_plan_step_statuses_from_tasks()
                    return task

        # Fall back to title contains match.
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

        # Fallback plan when model output is invalid.
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

    def extract_task_command(self, user_message):
        msg = user_message.strip()
        msg_lower = msg.lower()

        # List tasks
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

        # Add task
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

        # Mark complete / done
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

        # Direct math expression, e.g. "2+2" or "(10-2) * 3"
        if re.fullmatch(r"[\d\.\+\-\*/%\(\)\s]+", msg) and re.search(r"[\+\-\*/%]", msg):
            return msg.strip()

        # "calculate 2+2" / "solve: (5*4)-3"
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

        # Code block form
        block = re.search(r"```(?:python)?\s*([\s\S]*?)```", msg, re.IGNORECASE)
        if block:
            return block.group(1).strip()

        # Inline form: "run python: print('hi')"
        inline = re.search(r"(?:run|execute|test)\s+python\s*[:\-]?\s*([\s\S]+)", msg, re.IGNORECASE)
        if inline:
            return inline.group(1).strip()

        return None

    def process_request(self, user_message):
        self.extract_long_term_memory(user_message)
        self.add_to_short_term("user", user_message)
        self.sync_plan_step_statuses_from_tasks()

        plan_cmd = self.extract_plan_command(user_message)
        task_cmd = self.extract_task_command(user_message)
        expression = self.extract_math_expression(user_message)
        code = self.extract_python_code(user_message)

        if plan_cmd:
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
        elif expression:
            result = calculator(expression)
            final_reply = f"Result: {result}"
        elif code:
            result = run_python_code(code)
            final_reply = f"\n--- Code Output ---\n{result}"
        else:
            final_reply = self.ask_ai()

        self.add_to_short_term("assistant", final_reply)
        self.save_memory()
        return final_reply


# =======================
# STEP 4 - CHAT LOOP
# =======================

model_name = os.getenv("OLLAMA_MODEL", "mistral")
assistant = AssistantWithMemory(model=model_name)
print(f"AI Assistant ({model_name}) with Python Tool + Memory (type 'exit' to quit)\n")

while True:
    msg = input("You: ")
    if msg.lower() == "exit":
        break
    reply = assistant.process_request(msg)
    print("AI:", reply)
