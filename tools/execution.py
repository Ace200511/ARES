import os
import sys
import ast
import json
import subprocess

SANDBOX = os.path.abspath("./sandbox")

def run_code(filename: str) -> str:
    try:
        target_path = os.path.abspath(os.path.join(SANDBOX, filename))
        if not target_path.startswith(SANDBOX):
            return json.dumps({
                "status": "error",
                "action": "run_code",
                "message": "Access denied: Execution file outside sandbox."
            })

        if not os.path.exists(target_path):
            return json.dumps({
                "status": "error",
                "action": "run_code",
                "message": f"File '{filename}' does not exist."
            })

        with open(target_path, "r", encoding="utf-8") as f:
            code_source = f.read()

        try:
            ast.parse(code_source)
        except SyntaxError as syn_err:
            return json.dumps({
                "status": "error",
                "stage": "ast_validation",
                "error_type": "SyntaxError",
                "line": syn_err.lineno,
                "message": syn_err.msg
            })

        result = subprocess.run(
            [sys.executable, target_path],
            cwd=SANDBOX,
            capture_output=True,
            text=True,
            timeout=10
        )

        return json.dumps({
            "status": "success" if result.returncode == 0 else "runtime_error",
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip()
        })

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "error",
            "action": "run_code",
            "message": "Execution timed out after 10 seconds (possible infinite loop)."
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "run_code", "message": str(e)})

RUN_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_code",
        "description": "Executes a Python file from the sandbox, returning stdout, stderr, and exit codes.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the Python file to run (e.g., 'main.py')."
                }
            },
            "required": ["filename"]
        }
    }
}