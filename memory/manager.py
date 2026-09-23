import os
import json
from typing import Tuple, List, Optional, Dict, Any

MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
ACTIONS_FILE = os.path.join(MEMORY_DIR, "actions.json")
CONTEXT_FILE = os.path.join(MEMORY_DIR, "context.json")


def init_memory() -> None:
    os.makedirs(MEMORY_DIR, exist_ok=True)

    if not os.path.exists(ACTIONS_FILE) or os.path.getsize(ACTIONS_FILE) == 0:
        with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)

    if not os.path.exists(CONTEXT_FILE) or os.path.getsize(CONTEXT_FILE) == 0:
        default_context = {
            "agent_name": "ARES",
            "runtime": "Offline Edge (Localhost)",
            "sandbox_path": "./sandbox",
            "user_preferences": {
                "auto_syntax_check": True,
                "max_execution_timeout_sec": 10
            }
        }
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(default_context, f, indent=2)


def load_context() -> Dict[str, Any]:
    """Reads system, user, and environment context for prompt injection."""
    if not os.path.exists(CONTEXT_FILE):
        init_memory()

    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def update_context(key: str, value: Any) -> bool:
    """Updates a single top-level key inside context.json."""
    context = load_context()
    context[key] = value
    try:
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(context, f, indent=2)
        return True
    except Exception:
        return False


def check_memory_gate(task: str) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    Evaluates whether the incoming task has an approved cached macro (+1)
    or recorded anti-patterns (-1).

    Returns:
        (cached_actions, anti_patterns)
        - cached_actions: List of action dicts if exact +1 match exists, else None.
        - anti_patterns: List of failed action chains to avoid.
    """
    if not os.path.exists(ACTIONS_FILE):
        init_memory()

    clean_task = task.lower().strip()
    try:
        with open(ACTIONS_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (json.JSONDecodeError, Exception):
        records = []

    anti_patterns = []

    for item in records:
        recorded_task = item.get("task", "").lower().strip()
        if recorded_task == clean_task:
            score = item.get("score", 0)
            if score == 1:
                # Cache Hit: Deterministic replay ready
                return item.get("actions", []), []
            elif score == -1:
                # Anti-Pattern: Store failed steps to feed into system prompt
                anti_patterns.extend(item.get("actions", []))

    return None, anti_patterns


def commit_feedback(task: str, actions: List[Dict[str, Any]], score: int) -> bool:
    """
    Records human reinforcement (+1 or -1) into actions.json.
    Replaces older entries of the identical task to keep the database clean.
    """
    if not os.path.exists(ACTIONS_FILE):
        init_memory()

    try:
        with open(ACTIONS_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (json.JSONDecodeError, Exception):
        records = []

    clean_task = task.strip()

    records = [
        r for r in records 
        if r.get("task", "").lower().strip() != clean_task.lower()
    ]

    records.append({
        "task": clean_task,
        "score": 1 if score > 0 else -1,
        "actions": actions
    })

    try:
        with open(ACTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)
        return True
    except Exception:
        return False