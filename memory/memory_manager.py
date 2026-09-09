import os
import json

class MemoryManager:
    def __init__(self):
        self.history=[]
        self.load_from_file()

    def add_message(self,role,content):
        self.history.append({"role":role,"content":content})

    def get_history(self):
        return self.history

    def save_to_file(self, filepath="data/chat_history.json"):
        with open(filepath, "w") as f:
            json.dump(self.history, f)

    def load_from_file(self, filepath= "data/chat_history.json"):
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                self.history = json.load(f)