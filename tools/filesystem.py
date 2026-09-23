import os
import json
import shutil

SANDBOX = os.path.abspath("./sandbox")

def _resolve_safe_path(rel_path: str) -> str:
    target = os.path.abspath(os.path.join(SANDBOX, rel_path))
    if not target.startswith(SANDBOX):
        raise PermissionError(f"Access denied: '{rel_path}' escapes the sandbox.")
    return target

def write_file(filename: str, content: str) -> str:
    try:
        target_path = _resolve_safe_path(filename)
        parent_dir = os.path.dirname(target_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(content)

        return json.dumps({
            "status": "success",
            "action": "write_file",
            "filename": filename,
            "bytes_written": len(content)
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "write_file", "message": str(e)})

def read_file(filename: str) -> str:
    try:
        target_path = _resolve_safe_path(filename)
        if not os.path.exists(target_path):
            return json.dumps({
                "status": "error",
                "action": "read_file",
                "message": f"File '{filename}' does not exist."
            })

        with open(target_path, "r", encoding="utf-8") as f:
            data = f.read()

        return json.dumps({
            "status": "success",
            "action": "read_file",
            "filename": filename,
            "content": data
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "read_file", "message": str(e)})

def create_folder(folder_name: str) -> str:
    try:
        target_path = _resolve_safe_path(folder_name)
        os.makedirs(target_path, exist_ok=True)
        return json.dumps({
            "status": "success",
            "action": "create_folder",
            "folder_name": folder_name
        })
    except Exception as e:
        return json.dumps({"status": "error", "action": "create_folder", "message": str(e)})

def delete_path(target_name:str) -> str:
    try:
        target_path = _resolve_safe_path(target_name)

        if not os.pathexists(target_path):
            return json.dumps({
                "status": "error",
                "action": "delete_path",
                "message": f"Target '{target_name}' does not exist."
            })

        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
            item_type = "folder"
        else:
            os.remove(target_path)
            item_type = "file"

        return json.dumps({
            "status": "success",
            "action": "delete_path",
            "target": target_name,
            "type": item_type
        })                    


    except Exception as e:
        return json.dumps({
            "status": "error",
            "action": "delete_path",
            "messsage": str(e)
        })

WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Writes or overwrites text or code into a file inside the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Relative filename with extension (e.g., 'script.py', 'data.json', 'notes.txt')."
                },
                "content": {
                    "type": "string",
                    "description": "The exact raw text or code to write into the file."
                }
            },
            "required": ["filename", "content"]
        }
    }
}

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Reads and returns content from an existing file in the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Relative filename to read (e.g., 'script.py')."
                }
            },
            "required": ["filename"]
        }
    }
}

CREATE_FOLDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_folder",
        "description": "Creates a directory or nested folders inside the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_name": {
                    "type": "string",
                    "description": "Relative folder path to create (e.g., 'logs' or 'output/data')."
                }
            },
            "required": ["folder_name"]
        }
    }
}

DELETE_PATH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_path",
        "description": "Deletes a file or folder inside the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_name": {
                    "type": "string",
                    "description": "Relative path of the file or folder to delete (e.g., 'main.py' or 'temp_logs')"
                }
            },
            "required": ["target_name"]
        }
    }
}