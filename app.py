import os
import json
import threading
import ollama

from ui.window import AresWindow
from tools import ALL_TOOL_SCHEMAS, TOOL_REGISTRY
from memory import (
    init_memory,
    load_context,
    check_memory_gate,
    commit_feedback
)

# Configuration
MODEL_NAME = "qwen2.5:3b"  # or "phi3:mini"
SANDBOX_DIR = os.path.abspath("./sandbox")
MAX_REACT_STEPS = 30       # Circuit breaker to prevent infinite tool loops

# Session tracking for human feedback
active_task = ""
active_action_trail = []


def run_agent_pipeline(task: str, gui: AresWindow):
    """
    Main worker executing on a background thread.
    1. Checks Memory Gate for cached macros (+1).
    2. Injects anti-patterns (-1) if previously failed.
    3. Runs the multi-turn ReAct loop with Ollama.
    """
    global active_task, active_action_trail
    active_task = task
    active_action_trail = []

    gui.clear_terminal()
    gui.log(f">> DIRECTIVE: {task}", tag="cyan")

    # -------------------------------------------------------------
    # 1. MEMORY GATE: Check for cached macro or anti-patterns
    # -------------------------------------------------------------
    cached_actions, anti_patterns = check_memory_gate(task)

    if cached_actions is not None:
        gui.log("\n[MEMORY GATE HIT] Approved macro found (+1). Bypassing LLM...", tag="yellow")
        gui.set_status("REPLAYING MACRO", color="#CE9178")

        for step in cached_actions:
            tool_name = step.get("tool")
            tool_args = step.get("args", {})
            gui.log(f">> Executing Macro Step: {tool_name}({tool_args})", tag="dim")

            executor = TOOL_REGISTRY.get(tool_name)
            if executor:
                obs = executor(**tool_args)
                gui.log(f"[OBSERVATION]: {obs}", tag="green")
            else:
                gui.log(f"[ERROR]: Tool '{tool_name}' not found.", tag="red")

            active_action_trail.append(step)

        gui.log("\n[SUCCESS] Deterministic macro replay complete.", tag="green")
        gui.set_status("AWAITING FEEDBACK", color="#4EC9B0")
        gui.enable_feedback()
        gui.enable_execution()
        return

    # -------------------------------------------------------------
    # 2. MEMORY GATE MISS: Prepare context & ReAct prompt
    # -------------------------------------------------------------
    gui.log("\n[MEMORY GATE MISS] Initiating autonomous ReAct reasoning...", tag="dim")
    gui.set_status("REASONING (REACT)", color="#E5C07B")

    context = load_context()
    system_prompt = (
        f"You are ARES, an autonomous offline agent running on edge hardware.\n"
        f"Environment Metadata: {json.dumps(context)}\n\n"
        f"DECISION RULES:\n"
        f"1. CHAT & QUESTIONS: For basic math (e.g., '2+2'), greetings, logic, or explanations, "
        f"answer directly in plain text. DO NOT CALL ANY TOOLS.\n"
        f"2. TOOL USAGE: ONLY call tools if the user explicitly instructs you to create, modify, "
        f"read, execute a file, or open an application.\n"
        f"3. All filesystem tools are strictly isolated inside './sandbox'.\n"
        f"4. Once your objective is met, output a final confirmation without calling further tools."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task}
    ]

    # Inject negative constraints if previous runs with this task failed
    if anti_patterns:
        gui.log(f"[ANTI-PATTERNS DETECTED] Injecting {len(anti_patterns)} prior failure(s) to avoid.", tag="red")
        messages.append({
            "role": "system",
            "content": f"CRITICAL CONSTRAINT: Do NOT repeat these failed action patterns: {json.dumps(anti_patterns)}"
        })

    step_count = 0

    while step_count < MAX_REACT_STEPS:
        step_count += 1

        try:
            response = ollama.chat(
                model=MODEL_NAME,
                messages=messages,
                tools=ALL_TOOL_SCHEMAS
            )
        except Exception as e:
            gui.log(f"\n[OLLAMA RUNTIME ERROR]: {str(e)}", tag="red")
            break

        msg = response.get("message", {})
        messages.append(msg)

        tool_calls = msg.get("tool_calls")

        if not tool_calls:
            final_text = msg.get("content", "").strip()
            if final_text:
                gui.log(f"\n[ARES]: {final_text}\n", tag="cyan")
            break

        for call in tool_calls:
            func = call.get("function", {})
            name = func.get("name")
            args = func.get("arguments", {})

            gui.log(f"\n>> ACTION: {name}({json.dumps(args)})", tag="yellow")
            active_action_trail.append({"tool": name, "args": args})

            executor = TOOL_REGISTRY.get(name)
            if executor:
                obs = executor(**args)
            else:
                obs = json.dumps({"status": "error", "message": f"Tool '{name}' not found."})

            is_success = '"status": "success"' in obs
            gui.log(f"[OBSERVATION]: {obs}", tag="green" if is_success else "red")

            messages.append({
                "role": "tool",
                "content": str(obs)
            })

    gui.set_status("TASK COMPLETE", color="#6A9955")
    gui.enable_feedback()
    gui.enable_execution()

def on_run_task(task_input: str):
    """Dispatches execution thread to keep UI completely responsive."""
    worker = threading.Thread(
        target=run_agent_pipeline,
        args=(task_input, app_window),
        daemon=True
    )
    worker.start()


def on_feedback_received(score: int):
    """Commits user reinforcement (+1 or -1) to actions.json."""
    global active_task, active_action_trail
    if active_task and active_action_trail:
        commit_feedback(active_task, active_action_trail, score)
        tag = "green" if score > 0 else "red"
        label = "+1 (Approved & Cached)" if score > 0 else "-1 (Logged Anti-Pattern)"
        app_window.log(f"\n[FEEDBACK PERSISTED]: Recorded {label} for '{active_task}'", tag=tag)
    app_window.set_status("IDLE", color="#4EC9B0")

if __name__ == "__main__":
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    init_memory()

    app_window = AresWindow(
        title="A.R.E.S. // Autonomous Reasoning & Experiential System",
        on_execute=on_run_task,
        on_feedback=on_feedback_received
    )
    app_window.start()