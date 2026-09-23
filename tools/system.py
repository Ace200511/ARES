import json
import subprocess

APP_WHITELIST = {
    "notepad": "notepad.exe",
    "calc": "calculator.exe",
}


def launch_app(app_name: str) -> str:
    """Launches an approved local desktop application."""
    clean_name = app_name.lower().strip()
    if clean_name not in APP_WHITELIST:
        return json.dumps({
            "status": "error",
            "action": "launch_app",
            "message": f"App '{app_name}' not allowed. Whitelist: {list(APP_WHITELIST.keys())}"
        })

    try:
        exe = APP_WHITELIST[clean_name]
        subprocess.Popen([exe])
        return json.dumps({
            "status": "success",
            "action": "launch_app",
            "app": clean_name
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "launch_app", "message": str(e)})


def close_app(app_name: str) -> str:
    """Closes an approved application process via taskkill."""
    clean_name = app_name.lower().strip()
    if clean_name not in APP_WHITELIST or clean_name == "explorer":
        return json.dumps({
            "status": "error",
            "action": "close_app",
            "message": f"App '{app_name}' cannot be terminated or is not whitelisted."
        })

    try:
        exe = APP_WHITELIST[clean_name]
        res = subprocess.run(["taskkill", "/f", "/im", exe], capture_output=True, text=True)
        return json.dumps({
            "status": "success" if res.returncode == 0 else "warning",
            "action": "close_app",
            "message": res.stdout.strip() or res.stderr.strip()
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "close_app", "message": str(e)})

LAUNCH_APP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "launch_app",
        "description": "Opens a local desktop application. Allowed: 'notepad','calc','VScode','browser'.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application identifier: 'notepad', 'calc', or 'explorer'."
                }
            },
            "required": ["app_name"]
        }
    }
}

CLOSE_APP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "close_app",
        "description": "Terminates a running whitelisted program ('notepad' or 'calc').",
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "The application to terminate: 'notepad' or 'calc'."
                }
            },
            "required": ["app_name"]
        }
    }
}