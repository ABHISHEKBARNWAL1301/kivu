"""Tools for working with the user: update_plan (a todo list) and ask_user."""

from __future__ import annotations

from typing import Protocol

from .registry import Tool

STATUSES = ["pending", "in_progress", "done"]


class TaskUI(Protocol):
    def plan(self, items: list[dict]) -> None: ...

    def ask_user(self, question: str) -> str | None: ...


def task_tools(ui: TaskUI) -> list[Tool]:
    
    def update_plan(items: list[dict]) -> str:
        for item in items:
            if item.get("status") not in STATUSES:
                raise ValueError(f"status must be one of {STATUSES}")
        ui.plan(items)
        return "plan updated"

    def ask_user(question: str) -> str:
        answer = ui.ask_user(question)
        if answer is None:
            return "the user isn't available; make a reasonable choice and say what you assumed"
        return answer or "(no answer)"

    return [
        Tool(
            "update_plan",
            "Show the user your plan for a multi-step task. Send the whole list each time; "
            "keep exactly one step in_progress and mark steps done as you finish them.",
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "status": {"type": "string", "enum": STATUSES},
                            },
                            "required": ["step", "status"],
                        },
                    }
                },
                "required": ["items"],
            },
            update_plan,
        ),
        Tool(
            "ask_user",
            "Ask the user a question and wait for the answer. Use it when the request is unclear "
            "or there are real choices to make, not for things you can find out with the tools.",
            {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
            ask_user,
        ),
    ]
