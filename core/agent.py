from memory.memory_manager import MemoryManager
from tools.terminal import execute_command

class Agent:
    def __init__(self, name="ARES"):
        self.name = name
        self.memory = MemoryManager()

    def run_task(self, task_description):
        self.memory.add_message("user",task_description)
        result_output = execute_command(task_description)
        self.memory.add_message("assistant",result_output)
        self.memory.save_to_file()
        return result_output