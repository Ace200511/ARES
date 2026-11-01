"""
Module G: 16-step Autonomous ReAct Engine with:
  1. Primary Orchestrator: qwen2.5-coder:1.5b (keep_alive="5m")
  2. Self-Correction Escalation: qwen2.5-coder:3b (triggered on exit_code != 0 or stderr)
  3. Cyclical Loop / Deep Planning Recovery: deepseek-r1:1.5b
  4. Rolling Context Compactor: qwen2.5:0.5b (triggered when tokens > 3,500)
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ares.agent.prompts import (
    COMPACTOR_SYSTEM_PROMPT,
    DEBUGGER_SYSTEM_PROMPT,
    REASONER_SYSTEM_PROMPT,
    build_orchestrator_system_prompt,
)
from ares.config import (
    COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_NUM_CTX,
    DEFAULT_TEMPERATURE,
    MAX_REACT_STEPS,
    MODEL_COMPACTOR,
    MODEL_DEBUGGER,
    MODEL_ORCHESTRATOR,
    MODEL_REASONER,
)
from ares.llm.manager import ModelManager
from ares.tools.coder import CodeDoctor
from ares.tools.registry import ToolRegistry

logger = logging.getLogger("ares.agent.react_engine")


class ReActEngine:
    """
    Executes the 16-step ReAct loop while coordinating dynamic single-model VRAM swapping.
    """

    ACTION_JSON_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
    CODE_BLOCK_PATTERN = re.compile(r"```(?:python)?\s*(.*?)\s*```", re.DOTALL)

    def __init__(
        self,
        model_manager: Optional[ModelManager] = None,
        tool_registry: Optional[ToolRegistry] = None,
        max_steps: int = MAX_REACT_STEPS,
        verbose: bool = True,
    ) -> None:
        self.llm = model_manager or ModelManager()
        self.registry = tool_registry or ToolRegistry()
        self.max_steps = max_steps
        self.verbose = verbose

    def _log(self, header: str, body: str) -> None:
        if self.verbose:
            print(f"\n[{header}]\n{body}", flush=True)

    @classmethod
    def parse_thought_and_action(cls, llm_output: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """
        Strictly parses `Thought:` and `Action:` JSON blocks from the orchestrator's output.
        Includes fallback brace extraction if markdown fences were omitted.
        """
        thought = ""
        thought_match = re.search(r"Thought:\s*(.*?)(?:Action:|$)", llm_output, re.DOTALL)
        if thought_match:
            thought = thought_match.group(1).strip()

        # Primary extraction: fenced ```json ... ``` block
        json_match = cls.ACTION_JSON_PATTERN.search(llm_output)
        candidates: List[str] = []
        if json_match:
            candidates.append(json_match.group(1).strip())

        # Fallback extraction: balanced outer braces after "Action:"
        action_split = llm_output.split("Action:", 1)
        search_region = action_split[1] if len(action_split) > 1 else llm_output
        first_brace = search_region.find("{")
        last_brace = search_region.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidates.append(search_region[first_brace : last_brace + 1])

        for raw_json in candidates:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict) and "tool" in parsed:
                    if "args" not in parsed or not isinstance(parsed["args"], dict):
                        parsed["args"] = {}
                    return thought, parsed
            except json.JSONDecodeError:
                continue

        return thought, None

    def _maybe_compact_context(
        self,
        messages: List[Dict[str, str]],
        original_goal: str,
    ) -> List[Dict[str, str]]:
        """
        Context Rolling Compaction:
        If total messages exceed 3,500 tokens, cold-loads `qwen2.5:0.5b` to summarize
        steps 1 through (N-3) into a compact state summary, preserving the system prompt,
        original user intent, and the final 3 messages.
        """
        estimated_tokens = self.llm.estimate_tokens(messages)
        if estimated_tokens <= COMPACTION_TOKEN_THRESHOLD or len(messages) <= 5:
            return messages

        self._log(
            "CONTEXT COMPACTOR",
            f"Token count ({estimated_tokens}) exceeded {COMPACTION_TOKEN_THRESHOLD}. "
            f"Swapping to {MODEL_COMPACTOR}...",
        )

        # Preserve index 0 (system prompt), index 1 (user goal), and last 3 messages
        intermediate_msgs = messages[2:-3]
        serialized_history = "\n".join(
            f"[{m['role'].upper()}]: {m['content']}" for m in intermediate_msgs
        )

        compactor_messages = [
            {"role": "system", "content": COMPACTOR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Original User Goal: {original_goal}\n\n"
                    f"Intermediate Steps to Summarize:\n{serialized_history}"
                ),
            },
        ]

        summary_text = self.llm.chat(
            messages=compactor_messages,
            model=MODEL_COMPACTOR,
            num_ctx=DEFAULT_NUM_CTX,
            temperature=0.0,
            keep_alive="0s",
        )
        # Explicitly evict compactor before returning to orchestrator
        self.llm.unload_model(MODEL_COMPACTOR)

        compacted: List[Dict[str, str]] = [
            messages[0],
            messages[1],
            {
                "role": "user",
                "content": (
                    "Observation: [COMPRESSED EXECUTION STATE SUMMARY]\n"
                    f"{summary_text}"
                ),
            },
            *messages[-3:],
        ]
        new_tokens = self.llm.estimate_tokens(compacted)
        self._log(
            "CONTEXT COMPACTOR",
            f"Compacted context from {estimated_tokens} -> {new_tokens} tokens.",
        )
        return compacted

    def _run_autonomous_debugger(
        self,
        file_path: str,
        stderr_trace: str,
        user_goal: str,
    ) -> Dict[str, Any]:
        """
        Self-Correction Escalation:
        Cold-loads `qwen2.5-coder:3b`, feeds the numbered failing code and exact stderr snippet,
        extracts the patched code, writes it directly to disk, and re-verifies execution.
        """
        self._log(
            "AUTONOMOUS DEBUGGER",
            f"Execution failed on '{file_path}'. Escalating to {MODEL_DEBUGGER}...",
        )
        numbered_code = CodeDoctor.read_numbered_source(file_path)
        if not numbered_code:
            return {
                "exit_code": 1,
                "stdout": "",
                "stderr": f"Debugger could not read source file: {file_path}",
            }

        debugger_messages = [
            {"role": "system", "content": DEBUGGER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Task Objective: {user_goal}\n"
                    f"Target File: {file_path}\n\n"
                    f"Numbered Failing Source Code:\n```python\n{numbered_code}\n```\n\n"
                    f"Exact Traceback / Compiler Error:\n```\n{stderr_trace}\n```\n\n"
                    "Provide the complete corrected Python file inside a ```python ... ``` block."
                ),
            },
        ]

        raw_patch = self.llm.chat(
            messages=debugger_messages,
            model=MODEL_DEBUGGER,
            num_ctx=DEFAULT_NUM_CTX,
            temperature=DEFAULT_TEMPERATURE,
            keep_alive="0s",
        )
        # Immediately flush 3B model from VRAM after generating the patch
        self.llm.unload_model(MODEL_DEBUGGER)

        code_match = self.CODE_BLOCK_PATTERN.search(raw_patch)
        patched_code = code_match.group(1) if code_match else raw_patch
        CodeDoctor.direct_write_code(file_path, patched_code)

        # Open in VS Code if error line number is present in traceback
        line_match = re.search(r"line\s+(\d+)", stderr_trace)
        if line_match:
            try:
                CodeDoctor.open_in_vscode(file_path, line=int(line_match.group(1)))
            except Exception:
                pass

        rerun_result = CodeDoctor.verify_and_run(file_path)
        self._log(
            "AUTONOMOUS DEBUGGER RESULT",
            json.dumps(rerun_result, indent=2),
        )
        return rerun_result

    def _run_deep_reasoner(
        self,
        user_goal: str,
        action_history: List[Tuple[str, str]],
        recent_error: str,
    ) -> str:
        """
        Cold-loads `deepseek-r1:1.5b` when a cyclical loop (repeated identical actions)
        or multi-variable planning stall is detected.
        """
        self._log(
            "DEEP REASONER",
            f"Cyclical loop or complex planning trigger detected. Swapping to {MODEL_REASONER}...",
        )
        formatted_trace = "\n".join(
            f"{idx + 1}. tool={t_name}, args={t_args}"
            for idx, (t_name, t_args) in enumerate(action_history[-6:])
        )
        reasoner_messages = [
            {"role": "system", "content": REASONER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User Objective: {user_goal}\n\n"
                    f"Recent Action Trace (Loop Detected):\n{formatted_trace}\n\n"
                    f"Latest Observation / Error:\n{recent_error}\n\n"
                    "Formulate a concrete 3-step recovery plan using alternate tools or arguments."
                ),
            },
        ]

        plan = self.llm.chat(
            messages=reasoner_messages,
            model=MODEL_REASONER,
            num_ctx=DEFAULT_NUM_CTX,
            temperature=DEFAULT_TEMPERATURE,
            keep_alive="0s",
        )
        self.llm.unload_model(MODEL_REASONER)
        # Strip internal <think>...</think> blocks if present so the 1.5B orchestrator gets a clean directive
        clean_plan = re.sub(r"<think>.*?</think>", "", plan, flags=re.DOTALL).strip()
        self._log("DEEP REASONER PLAN", clean_plan)
        return clean_plan

    @staticmethod
    def _is_complex_planning_query(query: str) -> bool:
        """Detects multi-variable algorithmic or architectural planning queries."""
        keywords = (
            "algorithm",
            "optimize",
            "schedule",
            "dynamic programming",
            "graph",
            "architecture",
            "multi-step",
            "constraint",
        )
        q_lower = query.lower()
        matches = sum(1 for kw in keywords if kw in q_lower)
        return matches >= 2 or len(query.split()) > 65

    def run(self, user_query: str) -> str:
        """
        Executes up to 16 autonomous ReAct steps for `user_query` and returns the final answer.
        """
        top_tools = self.registry.get_top_tools(user_query, top_k=3)
        system_prompt = build_orchestrator_system_prompt(top_tools)

        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        action_history: List[Tuple[str, str]] = []
        last_observation_str: str = ""

        # Optional pre-planning pass with deepseek-r1:1.5b for complex multi-variable tasks
        if self._is_complex_planning_query(user_query):
            initial_plan = self._run_deep_reasoner(user_query, [], "Initial complex planning request.")
            messages.append(
                {
                    "role": "user",
                    "content": f"Observation: [STRATEGIC PLAN FROM REASONER]\n{initial_plan}",
                }
            )

        for step in range(1, self.max_steps + 1):
            # Step 1: Rolling context compaction check (>3,500 tokens)
            messages = self._maybe_compact_context(messages, original_goal=user_query)

            # Step 2: Query primary orchestrator (qwen2.5-coder:1.5b)
            raw_response = self.llm.chat(
                messages=messages,
                model=MODEL_ORCHESTRATOR,
                num_ctx=DEFAULT_NUM_CTX,
                temperature=DEFAULT_TEMPERATURE,
            )
            self._log(f"STEP {step}/{self.max_steps} - ORCHESTRATOR", raw_response)
            messages.append({"role": "assistant", "content": raw_response})

            thought, action_payload = self.parse_thought_and_action(raw_response)
            if action_payload is None:
                correction_msg = (
                    "Observation: [FORMAT ERROR] Your response did not contain a valid JSON "
                    "Action block. Respond strictly with 'Thought:' followed by 'Action:' and "
                    "a valid ```json {\"tool\": \"...\", \"args\": {...}} ``` block."
                )
                messages.append({"role": "user", "content": correction_msg})
                continue

            tool_name: str = str(action_payload.get("tool", "")).strip()
            tool_args: Dict[str, Any] = action_payload.get("args", {})

            # Step 3: Check exit condition
            if tool_name == "finish":
                final_answer = str(tool_args.get("final_answer", thought or "Task completed."))
                self._log("FINAL ANSWER", final_answer)
                return final_answer

            # Step 4: Cyclical loop detection (identical tool + args repeated)
            canonical_args = json.dumps(tool_args, sort_keys=True)
            action_signature = (tool_name, canonical_args)
            action_history.append(action_signature)

            if len(action_history) >= 2 and action_history[-1] == action_history[-2]:
                recovery_plan = self._run_deep_reasoner(
                    user_goal=user_query,
                    action_history=action_history,
                    recent_error=last_observation_str,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Observation: [LOOP DETECTED] You repeated the exact same action. "
                            f"Follow this recovery plan instead:\n{recovery_plan}"
                        ),
                    }
                )
                continue

            # Step 5: Execute tool via CPU Registry
            exec_result = self.registry.execute_tool(tool_name, tool_args)
            self._log(f"STEP {step} - OBSERVATION ({tool_name})", json.dumps(exec_result, indent=2))

            # Step 6: Self-Correction Escalation to qwen2.5-coder:3b on code failure
            exit_code = int(exec_result.get("exit_code", 0))
            stderr_text = str(exec_result.get("stderr", "")).strip()
            target_file = exec_result.get("file_path")

            if (exit_code != 0 or stderr_text) and tool_name in ("verify_and_run", "execute_python") and target_file:
                patched_result = self._run_autonomous_debugger(
                    file_path=str(target_file),
                    stderr_trace=stderr_text or f"Process exited with code {exit_code}",
                    user_goal=user_query,
                )
                exec_result = patched_result

            last_observation_str = json.dumps(exec_result, ensure_ascii=False)
            messages.append({"role": "user", "content": f"Observation: {last_observation_str}"})

        fallback_summary = (
            f"Reached maximum step limit ({self.max_steps} iterations). "
            f"Last observation: {last_observation_str}"
        )
        self._log("MAX STEPS REACHED", fallback_summary)
        return fallback_summary