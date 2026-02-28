import json

import ollama


class AutonomousExecutor:
    def __init__(self, assistant):
        self.assistant = assistant

    def status_text(self):
        plans = [p for p in self.assistant.memory.get("plans", []) if p.get("status") != "completed"]
        pending_steps = 0
        for plan in plans:
            for step in plan.get("steps", []):
                if step.get("status") == "pending":
                    pending_steps += 1
        return f"Autonomy status: {len(plans)} active plans, {pending_steps} pending steps."

    def run_cycle(self):
        plan, step = self._next_pending_step()
        if not plan or not step:
            return "No pending steps in active plans."

        decision = self._decide_action(plan, step)
        return self._execute_decision(plan, step, decision)

    def _next_pending_step(self):
        for plan in self.assistant.memory.get("plans", []):
            if plan.get("status") == "completed":
                continue
            for step in plan.get("steps", []):
                if step.get("status") == "pending":
                    return plan, step
        return None, None

    def _decide_action(self, plan, step):
        prompt = (
            "You are an autonomous execution planner.\n"
            "Choose exactly one safe next action for the current plan step.\n"
            "Rules:\n"
            "- Keep actions controlled and small.\n"
            "- Never suggest deleting files unless explicitly required by the step.\n"
            "- Terminal commands need user confirmation before execution.\n"
            "- Use only relative workspace paths for file actions.\n"
            'Return ONLY JSON with schema: {"action":"respond|complete_step|complete_task|file|command",'
            '"message":"...", "task_query":"...", "file_command":{"action":"read|create|edit|append|delete",'
            '"path":"...", "content":"..."}, "command":"...", "mark_step_completed":false}\n'
            f"Goal: {plan.get('goal', '')}\n"
            f"Step: {step.get('description', '')}\n"
        )
        try:
            response = ollama.chat(
                model=self.assistant.model,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response["message"]["content"]
            payload = self.assistant._extract_json_object(content)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass

        return {"action": "respond", "message": "I could not infer a safe autonomous action for this step."}

    def _execute_decision(self, plan, step, decision):
        action = str(decision.get("action", "respond")).strip().lower()
        plan_id = plan.get("id")
        step_no = step.get("step")

        if action == "complete_step":
            if isinstance(plan_id, int) and isinstance(step_no, int) and self.assistant.mark_plan_step_completed(plan_id, step_no):
                return f"Autonomous cycle completed Plan #{plan_id} Step {step_no}."
            return "Autonomous cycle could not mark the step complete."

        if action == "complete_task":
            query = str(decision.get("task_query", "")).strip()
            if not query:
                return "Autonomous cycle skipped task completion because no task query was provided."
            task = self.assistant.complete_task(query)
            if not task:
                return f"Autonomous cycle could not find a task matching: {query}"
            if step.get("task_id") == task.get("id") and isinstance(plan_id, int) and isinstance(step_no, int):
                self.assistant.mark_plan_step_completed(plan_id, step_no)
            return f"Autonomous cycle marked task #{task.get('id')} as complete."

        if action == "file":
            file_command = decision.get("file_command")
            if not isinstance(file_command, dict):
                return "Autonomous cycle received an invalid file action."
            file_result = self.assistant.execute_file_command(file_command)
            if (
                bool(decision.get("mark_step_completed"))
                and "Reply 'yes'" not in file_result
                and not file_result.startswith("I could not")
                and not file_result.startswith("File operation error")
                and isinstance(plan_id, int)
                and isinstance(step_no, int)
            ):
                self.assistant.mark_plan_step_completed(plan_id, step_no)
            return f"Autonomous cycle file action result:\n{file_result}"

        if action == "command":
            command = str(decision.get("command", "")).strip()
            if not command:
                return "Autonomous cycle skipped command execution because no command was provided."
            payload = {"command": command, "plan_id": plan_id, "step": step_no}
            self.assistant._set_pending_action("system_command", payload)
            return (
                f"Autonomous cycle proposes command: {command}\n"
                "Reply 'yes' to execute it or 'no' to cancel."
            )

        message = str(decision.get("message", "")).strip()
        if not message:
            message = "Autonomous cycle chose to wait for more user guidance."
        return f"Autonomous cycle note for Plan #{plan_id} Step {step_no}: {message}"
