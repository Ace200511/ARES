import customtkinter as ctk
import tkinter as tk
from typing import Callable, Optional

# Global appearance configuration
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class AresWindow:
    def __init__(
        self,
        title: str = "A.R.E.S. // Edge Autonomous Agent",
        on_execute: Optional[Callable[[str], None]] = None,
        on_feedback: Optional[Callable[[int], None]] = None,
    ):
        self.on_execute = on_execute
        self.on_feedback = on_feedback

        self.root = ctk.CTk()
        self.root.title(title)
        self.root.geometry("1000x780")
        self.root.minsize(820, 600)
        self.root.configure(fg_color="#0E1117")

        self._build_header()
        self._build_input_panel()
        self._build_terminal_view()
        self._build_feedback_bar()

    # -------------------------------------------------------------
    # LAYOUT BUILDERS
    # -------------------------------------------------------------

    def _build_header(self):
        header_card = ctk.CTkFrame(
            self.root,
            corner_radius=12,
            fg_color="#161B22",
            border_width=1,
            border_color="#21262D"
        )
        header_card.pack(fill="x", padx=18, pady=(16, 8))

        # Title & Subtitle container
        left_box = ctk.CTkFrame(header_card, fg_color="transparent")
        left_box.pack(side="left", padx=16, pady=12)

        lbl_title = ctk.CTkLabel(
            left_box,
            text="A.R.E.S.",
            font=ctk.CTkFont(family="Consolas", size=20, weight="bold"),
            text_color="#58A6FF"
        )
        lbl_title.pack(side="left")

        lbl_sub = ctk.CTkLabel(
            left_box,
            text="  // AUTONOMOUS REASONING & EXPERIENTIAL SYSTEM",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8B949E"
        )
        lbl_sub.pack(side="left")

        # Right badges
        right_box = ctk.CTkFrame(header_card, fg_color="transparent")
        right_box.pack(side="right", padx=16, pady=12)

        self.lbl_status = ctk.CTkLabel(
            right_box,
            text="● SYSTEM IDLE",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            text_color="#7EE787"
        )
        self.lbl_status.pack(side="right")

        badge = ctk.CTkLabel(
            right_box,
            text="OFFLINE EDGE RUNTIME",
            font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"),
            fg_color="#1F2A37",
            text_color="#38BDF8",
            corner_radius=6,
            padx=10,
            pady=4
        )
        badge.pack(side="right", padx=(0, 14))

    def _build_input_panel(self):
        input_card = ctk.CTkFrame(
            self.root,
            corner_radius=12,
            fg_color="#161B22",
            border_width=1,
            border_color="#21262D"
        )
        input_card.pack(fill="x", padx=18, pady=8)

        lbl_input = ctk.CTkLabel(
            input_card,
            text="TASK DIRECTIVE",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8B949E"
        )
        lbl_input.pack(anchor="w", padx=16, pady=(10, 4))

        row = ctk.CTkFrame(input_card, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 12))

        self.entry_task = ctk.CTkEntry(
            row,
            placeholder_text="e.g. Write a python script to test prime generation and run it...",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            fg_color="#0D1117",
            border_color="#30363D",
            text_color="#F0F6FC",
            placeholder_text_color="#6E7681",
            height=42,
            corner_radius=8
        )
        self.entry_task.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.entry_task.bind("<Return>", lambda e: self._handle_run())

        self.btn_run = ctk.CTkButton(
            row,
            text="⚡ EXECUTE",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            fg_color="#238636",
            hover_color="#2EA043",
            text_color="#FFFFFF",
            height=42,
            width=130,
            corner_radius=8,
            command=self._handle_run
        )
        self.btn_run.pack(side="right")

    def _build_terminal_view(self):
        term_card = ctk.CTkFrame(
            self.root,
            corner_radius=12,
            fg_color="#161B22",
            border_width=1,
            border_color="#21262D"
        )
        term_card.pack(fill="both", expand=True, padx=18, pady=8)

        # Header for the terminal card
        term_header = ctk.CTkFrame(term_card, fg_color="transparent")
        term_header.pack(fill="x", padx=16, pady=(10, 6))

        ctk.CTkLabel(
            term_header,
            text="TERMINAL TRACE & OBSERVATION STREAM",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8B949E"
        ).pack(side="left")

        # The scrollable terminal body
        self.terminal = ctk.CTkTextbox(
            term_card,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="#0D1117",
            text_color="#C9D1D9",
            corner_radius=8,
            border_width=1,
            border_color="#30363D",
            wrap="word",
            padx=14,
            pady=14
        )
        self.terminal.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        # Syntax color tag configurations
        self.terminal.tag_config("cyan", foreground="#58A6FF")
        self.terminal.tag_config("green", foreground="#7EE787")
        self.terminal.tag_config("yellow", foreground="#D29922")
        self.terminal.tag_config("red", foreground="#F85149")
        self.terminal.tag_config("dim", foreground="#8B949E")

    def _build_feedback_bar(self):
        bar = ctk.CTkFrame(
            self.root,
            corner_radius=12,
            fg_color="#161B22",
            border_width=1,
            border_color="#21262D"
        )
        bar.pack(fill="x", padx=18, pady=(8, 16))

        bar_inner = ctk.CTkFrame(bar, fg_color="transparent")
        bar_inner.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(
            bar_inner,
            text="EXPERIENTIAL REINFORCEMENT:",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color="#8B949E"
        ).pack(side="left")

        self.btn_neg = ctk.CTkButton(
            bar_inner,
            text="✖ REJECT (-1 ANTI-PATTERN)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#21262D",
            hover_color="#DA3633",
            text_color="#F85149",
            border_width=1,
            border_color="#F85149",
            height=36,
            corner_radius=8,
            state="disabled",
            command=lambda: self._handle_feedback(-1)
        )
        self.btn_neg.pack(side="right", padx=(8, 0))

        self.btn_pos = ctk.CTkButton(
            bar_inner,
            text="✔ APPROVE (+1 PERSIST MACRO)",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            fg_color="#21262D",
            hover_color="#238636",
            text_color="#7EE787",
            border_width=1,
            border_color="#7EE787",
            height=36,
            corner_radius=8,
            state="disabled",
            command=lambda: self._handle_feedback(1)
        )
        self.btn_pos.pack(side="right")

    # -------------------------------------------------------------
    # INTERNAL HANDLERS
    # -------------------------------------------------------------

    def _handle_run(self):
        task = self.entry_task.get().strip()
        if not task:
            return

        self.btn_run.configure(state="disabled")
        self.btn_pos.configure(state="disabled")
        self.btn_neg.configure(state="disabled")
        self.set_status("EXECUTING...", color="#D29922")

        if self.on_execute:
            self.on_execute(task)

    def _handle_feedback(self, score: int):
        self.btn_pos.configure(state="disabled")
        self.btn_neg.configure(state="disabled")

        if self.on_feedback:
            self.on_feedback(score)

    # -------------------------------------------------------------
    # THREAD-SAFE CONTROL METHODS FOR APP.PY
    # -------------------------------------------------------------

    def log(self, text: str, tag: Optional[str] = None):
        """Thread-safe append line to terminal view."""
        def _append():
            if tag:
                self.terminal.insert("end", text + "\n", tag)
            else:
                self.terminal.insert("end", text + "\n")
            self.terminal.see("end")
        self.root.after(0, _append)

    def clear_terminal(self):
        """Thread-safe wipe of terminal output."""
        self.root.after(0, lambda: self.terminal.delete("0.0", "end"))

    def set_status(self, text: str, color: str = "#7EE787"):
        """Thread-safe update of top right status indicator."""
        self.root.after(0, lambda: self.lbl_status.configure(text=f"● {text}", text_color=color))

    def enable_execution(self):
        """Thread-safe enable of the run button."""
        self.root.after(0, lambda: self.btn_run.configure(state="normal"))

    def enable_feedback(self):
        """Thread-safe activation of +1 and -1 evaluation buttons."""
        def _enable():
            self.btn_pos.configure(state="normal")
            self.btn_neg.configure(state="normal")
        self.root.after(0, _enable)

    def start(self):
        """Launches the UI event loop."""
        self.root.mainloop()