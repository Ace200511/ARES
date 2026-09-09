import subprocess
def execute_command(command: str):
    print(f"ARES is running:  {command}")

    result = subprocess.run(command,shell=True,capture_output = True, text = True)
    if result.returncode == 0:
        return result.stdout
    else:
        return result.stderr