import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional
import webbrowser

# Pin current directory to sys.path so `from ares_core import ...` resolves from any working dir
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
import requests
import uvicorn

from ares_core import (
    MODEL_COMPACTOR,
    MODEL_DEBUGGER,
    MODEL_ORCHESTRATOR,
    MODEL_REASONER,
    MODEL_VRAM_MB,
    OLLAMA_BASE_URL,
    OLLAMA_TAGS_URL,
    WORKSPACE_DIR,
    ModelManager,
    ReActEngine,
)


# ============================================================================
# 1. OLLAMA DAEMON AUTO-STARTER
# ============================================================================

def find_ollama_binary() -> Optional[str]:
    """
    Locates the Ollama executable across system PATH and standard Windows/Linux install paths.
    """
    path_bin = shutil.which("ollama") or shutil.which("ollama.exe")
    if path_bin:
        return path_bin

    candidate_paths = [
        Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(r"C:\Program Files\Ollama\ollama.exe"),
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ]
    for candidate in candidate_paths:
        if candidate.is_file():
            return str(candidate)
    return None


def ensure_ollama_daemon() -> bool:
    """
    Verifies http://127.0.0.1:11434 is responsive; if not, locates the Ollama binary
    via `find_ollama_binary()` and spawns `ollama serve` in the background.
    """
    try:
        resp = requests.get(OLLAMA_TAGS_URL, timeout=2)
        if resp.status_code == 200:
            print(f"[ARES] Ollama daemon online at {OLLAMA_BASE_URL}")
            return True
    except requests.RequestException:
        pass

    ollama_bin = find_ollama_binary()
    if not ollama_bin:
        print(
            "[ARES WARNING] Ollama daemon is offline and `ollama` binary was not found. "
            "Install Ollama or start it manually.",
            file=sys.stderr,
        )
        return False

    print(f"[ARES] Starting Ollama daemon using binary: {ollama_bin}")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    subprocess.Popen(
        [ollama_bin, "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        start_new_session=(sys.platform != "win32"),
    )

    for _ in range(25):
        time.sleep(0.4)
        try:
            resp = requests.get(OLLAMA_TAGS_URL, timeout=2)
            if resp.status_code == 200:
                print("[ARES] Ollama daemon started and verified.")
                return True
        except requests.RequestException:
            continue

    print("[ARES WARNING] Timed out waiting for Ollama daemon to respond.", file=sys.stderr)
    return False


# ============================================================================
# 2. EMBEDDED SINGLE-PAGE DARK-MODE DASHBOARD (TAILWIND CSS)
# ============================================================================

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ARES // Autonomous ReAct Execution System</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #090d16; color: #e2e8f0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    details > summary { list-style: none; }
    details > summary::-webkit-details-marker { display: none; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0f172a; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="h-screen flex flex-col overflow-hidden select-text">

  <!-- TOP TELEMETRY NAVBAR -->
  <header class="border-b border-slate-800 bg-slate-900/90 px-6 py-3 flex flex-wrap items-center justify-between gap-4">
    <div class="flex items-center gap-3">
      <div id="ws-dot" class="w-3 h-3 rounded-full bg-amber-400 animate-pulse"></div>
      <div>
        <h1 class="text-lg font-bold tracking-wider text-white flex items-center gap-2">
          ARES <span class="text-xs font-normal px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 mono">v2.0 FLAT-CORE</span>
        </h1>
        <p class="text-xs text-slate-400 mono" id="workspace-label">Workspace: C:\\Users\\abhishek\\ares\\workspace</p>
      </div>
    </div>

    <!-- VRAM TELEMETRY WIDGET -->
    <div class="flex items-center gap-6 bg-slate-950/90 border border-slate-800 rounded-lg px-4 py-2">
      <div>
        <div class="text-[11px] uppercase tracking-wider text-slate-400 font-semibold">Active VRAM Model</div>
        <div id="active-model-badge" class="text-sm font-bold text-emerald-400 mono">Idle (0 MB)</div>
      </div>
      <div class="w-44">
        <div class="flex justify-between text-[11px] mono text-slate-400 mb-1">
          <span id="vram-status-text">Ready</span>
          <span id="vram-usage-text">0 / 3200 MB</span>
        </div>
        <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
          <div id="vram-bar" class="h-full bg-emerald-500 transition-all duration-300" style="width: 0%"></div>
        </div>
      </div>
      <button onclick="flushVram()" class="text-xs px-3 py-1.5 rounded bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/30 mono transition">
        Flush VRAM
      </button>
    </div>
  </header>

  <!-- MAIN WORKSPACE GRID -->
  <main class="flex-1 grid grid-cols-1 lg:grid-cols-12 overflow-hidden">

    <!-- LEFT/CENTER: REACT STREAM & THINKING ACCORDIONS (8 COLS) -->
    <section class="lg:col-span-8 flex flex-col border-r border-slate-800 h-full overflow-hidden">
      <div id="chat-stream" class="flex-1 overflow-y-auto p-6 space-y-4">
        <div class="p-4 rounded-lg border border-slate-800 bg-slate-900/50 text-sm text-slate-300">
          <p class="font-semibold text-white mb-1">Sequential Single-Model Matrix Ready (4GB VRAM Ceiling)</p>
          <p class="text-xs text-slate-400 leading-relaxed">
            Orchestrator (<code class="text-indigo-300">qwen2.5-coder:1.5b</code>) &bull;
            Debugger (<code class="text-amber-300">qwen2.5-coder:3b</code>) &bull;
            Reasoner (<code class="text-purple-300">deepseek-r1:1.5b</code>) &bull;
            Compactor (<code class="text-cyan-300">qwen2.5:0.5b</code>).
            All model swaps enforce a 400ms CUDA eviction buffer (<code class="mono">keep_alive: 0</code>).
          </p>
        </div>
      </div>

      <!-- PROMPT INPUT FOOTER -->
      <div class="p-4 border-t border-slate-800 bg-slate-900/70">
        <form id="task-form" onsubmit="submitTask(event)" class="flex gap-3">
          <input
            id="task-input"
            type="text"
            placeholder="Enter autonomous task (e.g., 'Write a Python script that benchmarks prime sieve up to 100,000 and verify it')..."
            class="flex-1 bg-slate-950 border border-slate-700 focus:border-indigo-500 rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none"
            autocomplete="off"
          />
          <button
            id="send-btn"
            type="submit"
            class="px-5 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-semibold rounded-lg transition"
          >
            Execute
          </button>
        </form>
      </div>
    </section>

    <!-- RIGHT: LIVE TOOL EXECUTION LOGS (4 COLS) -->
    <aside class="lg:col-span-4 flex flex-col bg-slate-950/60 h-full overflow-hidden">
      <div class="px-4 py-3 border-b border-slate-800 flex items-center justify-between">
        <span class="text-xs font-semibold uppercase tracking-wider text-slate-400">Live Tool Execution Logs</span>
        <button onclick="clearToolLogs()" class="text-[11px] text-slate-500 hover:text-slate-300 mono">Clear</button>
      </div>
      <div id="tool-logs" class="flex-1 overflow-y-auto p-4 space-y-3 mono text-xs">
        <div class="text-slate-600 italic">Waiting for tool invocations...</div>
      </div>
    </aside>

  </main>

  <script>
    let ws = null;
    const chatStream = document.getElementById("chat-stream");
    const toolLogs = document.getElementById("tool-logs");
    const taskInput = document.getElementById("task-input");
    const sendBtn = document.getElementById("send-btn");
    const wsDot = document.getElementById("ws-dot");

    function escapeHtml(str) {
      return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }

    function connectWebSocket() {
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${window.location.host}/ws`);

      ws.onopen = () => {
        wsDot.className = "w-3 h-3 rounded-full bg-emerald-400";
      };

      ws.onclose = () => {
        wsDot.className = "w-3 h-3 rounded-full bg-rose-500";
        setTimeout(connectWebSocket, 2000);
      };

      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleServerEvent(data);
      };
    }

    function updateVramUi(activeModel, vramMb, statusText) {
      const badge = document.getElementById("active-model-badge");
      const statusEl = document.getElementById("vram-status-text");
      const usageEl = document.getElementById("vram-usage-text");
      const bar = document.getElementById("vram-bar");

      badge.textContent = `${activeModel} (${vramMb} MB)`;
      statusEl.textContent = statusText;
      usageEl.textContent = `${vramMb} / 3200 MB`;
      const pct = Math.min(100, Math.round((vramMb / 3200) * 100));
      bar.style.width = `${pct}%`;
      bar.className = `h-full transition-all duration-300 ${pct > 65 ? "bg-amber-400" : "bg-emerald-500"}`;
    }

    function handleServerEvent(ev) {
      if (ev.type === "vram") {
        updateVramUi(ev.active_model, ev.vram_mb, ev.status);
        return;
      }

      if (ev.type === "thought") {
        const details = document.createElement("details");
        details.open = true;
        details.className = "group rounded-lg border border-slate-800 bg-slate-900/60 overflow-hidden";
        details.innerHTML = `
          <summary class="cursor-pointer select-none px-4 py-2.5 bg-slate-900 flex items-center justify-between text-xs font-semibold text-indigo-300">
            <span class="flex items-center gap-2">
              <span class="px-2 py-0.5 rounded bg-indigo-500/20 border border-indigo-500/30 mono">Step ${escapeHtml(ev.step)}</span>
              <span class="mono text-slate-400">${escapeHtml(ev.model)}</span>
            </span>
            <span class="text-slate-500 group-open:rotate-180 transition-transform">&#9660;</span>
          </summary>
          <div class="p-4 text-sm text-slate-300 border-t border-slate-800/80 whitespace-pre-wrap leading-relaxed">${escapeHtml(ev.thought)}</div>
        `;
        chatStream.appendChild(details);
        chatStream.scrollTop = chatStream.scrollHeight;
        return;
      }

      if (ev.type === "action") {
        if (toolLogs.querySelector(".italic")) toolLogs.innerHTML = "";
        const card = document.createElement("div");
        card.className = "p-3 rounded border border-indigo-500/30 bg-indigo-950/20 space-y-1.5";
        card.innerHTML = `
          <div class="flex items-center justify-between text-indigo-300 font-semibold">
            <span>&#9654; ${escapeHtml(ev.tool)}</span>
            <span class="text-[10px] text-slate-400">Step ${escapeHtml(ev.step)}</span>
          </div>
          <pre class="text-[11px] text-slate-300 overflow-x-auto bg-slate-950 p-2 rounded">${escapeHtml(JSON.stringify(ev.args, null, 2))}</pre>
        `;
        toolLogs.appendChild(card);
        toolLogs.scrollTop = toolLogs.scrollHeight;
        return;
      }

      if (ev.type === "observation") {
        const res = ev.result || {};
        const ok = Number(res.exit_code || 0) === 0;
        const card = document.createElement("div");
        card.className = `p-3 rounded border ${ok ? "border-emerald-500/30 bg-emerald-950/15" : "border-rose-500/40 bg-rose-950/20"} space-y-1.5`;
        card.innerHTML = `
          <div class="flex items-center justify-between font-semibold ${ok ? "text-emerald-400" : "text-rose-400"}">
            <span>${ok ? "&#10003;" : "&#10007;"} ${escapeHtml(ev.tool)}</span>
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-slate-900">exit_code: ${escapeHtml(res.exit_code)}</span>
          </div>
          ${res.stdout ? `<pre class="text-[11px] text-slate-200 overflow-x-auto bg-slate-950 p-2 rounded max-h-40">${escapeHtml(res.stdout)}</pre>` : ""}
          ${res.stderr ? `<pre class="text-[11px] text-rose-300 overflow-x-auto bg-slate-950 p-2 rounded max-h-40">${escapeHtml(res.stderr)}</pre>` : ""}
        `;
        toolLogs.appendChild(card);
        toolLogs.scrollTop = toolLogs.scrollHeight;
        return;
      }

      if (ev.type === "finish") {
        const banner = document.createElement("div");
        banner.className = "p-4 rounded-lg border border-emerald-500/40 bg-emerald-950/20 text-sm text-emerald-100 space-y-1";
        banner.innerHTML = `
          <div class="text-xs font-bold uppercase tracking-wider text-emerald-400 mono">Task Completed</div>
          <div class="whitespace-pre-wrap leading-relaxed">${escapeHtml(ev.final_answer)}</div>
        `;
        chatStream.appendChild(banner);
        chatStream.scrollTop = chatStream.scrollHeight;
        sendBtn.disabled = false;
        sendBtn.textContent = "Execute";
        return;
      }

      if (ev.type === "error") {
        const errBox = document.createElement("div");
        errBox.className = "p-4 rounded-lg border border-rose-500/40 bg-rose-950/30 text-sm text-rose-200 mono";
        errBox.textContent = `Runtime Error: ${ev.message}`;
        chatStream.appendChild(errBox);
        chatStream.scrollTop = chatStream.scrollHeight;
        sendBtn.disabled = false;
        sendBtn.textContent = "Execute";
      }
    }

    function submitTask(e) {
      e.preventDefault();
      const task = taskInput.value.trim();
      if (!task || !ws || ws.readyState !== WebSocket.OPEN) return;

      const userBubble = document.createElement("div");
      userBubble.className = "p-3.5 rounded-lg bg-indigo-600/20 border border-indigo-500/30 text-sm text-white font-medium";
      userBubble.textContent = `> ${task}`;
      chatStream.appendChild(userBubble);
      chatStream.scrollTop = chatStream.scrollHeight;

      sendBtn.disabled = true;
      sendBtn.textContent = "Running...";
      ws.send(JSON.stringify({ task }));
      taskInput.value = "";
    }

    async function flushVram() {
      const res = await fetch("/api/vram/flush", { method: "POST" });
      const data = await res.json();
      updateVramUi("None (Flushed)", 0, data.status || "Flushed");
    }

    function clearToolLogs() {
      toolLogs.innerHTML = '<div class="text-slate-600 italic">Logs cleared.</div>';
    }

    connectWebSocket();
  </script>
</body>
</html>
"""


# ============================================================================
# 3. FASTAPI APPLICATION & WEBSOCKET STREAMING
# ============================================================================

app = FastAPI(title="ARES Consolidated Autonomous Agent", version="2.0.0")
global_model_manager = ModelManager()
SERVER_HOST = "127.0.0.1"
SERVER_PORT = int(os.getenv("ARES_PORT", "8001"))


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard() -> HTMLResponse:
    return HTMLResponse(content=DASHBOARD_HTML)


@app.get("/api/status")
async def get_status() -> JSONResponse:
    running = global_model_manager.get_running_models()
    active = running[0] if running else (global_model_manager.currently_loaded_model or "None")
    return JSONResponse(
        {
            "ollama_url": OLLAMA_BASE_URL,
            "workspace": str(WORKSPACE_DIR),
            "active_model": active,
            "vram_mb": MODEL_VRAM_MB.get(active, 0),
            "models": {
                "orchestrator": MODEL_ORCHESTRATOR,
                "debugger": MODEL_DEBUGGER,
                "reasoner": MODEL_REASONER,
                "compactor": MODEL_COMPACTOR,
            },
        }
    )


@app.post("/api/vram/flush")
async def flush_vram_endpoint() -> JSONResponse:
    await asyncio.to_thread(global_model_manager.unload_all)
    return JSONResponse({"status": "VRAM Flushed (400ms buffer applied)"})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    event_queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

    def thread_safe_Callback(event: Dict[str, Any]) -> None:
        loop.call_soon_threadsafe(event_queue.put_nowait, event)

    # Send initial workspace and VRAM state
    running = global_model_manager.get_running_models()
    current_model = running[0] if running else None
    await websocket.send_json(
        {
            "type": "vram",
            "status": "Ready",
            "active_model": current_model or "Idle",
            "vram_mb": MODEL_VRAM_MB.get(current_model or "", 0),
        }
    )

    try:
        while True:
            payload = await websocket.receive_json()
            user_task = str(payload.get("task", "")).strip()
            if not user_task:
                continue

            engine = ReActEngine(manager=global_model_manager)

            def run_engine_sync() -> None:
              step_number = 0
              active_model = MODEL_ORCHESTRATOR
              active_tool = ""
              for engine_event in engine.run(user_task):
                event_type = engine_event.get("type")
                if event_type == "status":
                  status = str(engine_event.get("data", ""))
                  if status.startswith("Step "):
                    status_parts = status.split(" | Model: ", 1)
                    try:
                      step_number = int(status_parts[0].split()[1].split("/", 1)[0])
                    except (IndexError, ValueError):
                      pass
                    if len(status_parts) == 2:
                      active_model = status_parts[1]
                elif event_type == "thought":
                  thread_safe_Callback(
                    {
                      "type": "thought",
                      "step": step_number,
                      "model": active_model,
                      "thought": engine_event.get("data", ""),
                    }
                  )
                elif event_type == "action":
                  active_tool = str(engine_event.get("tool", ""))
                  if active_tool != "finish":
                    thread_safe_Callback({**engine_event, "step": step_number})
                elif event_type == "observation":
                  observation = str(engine_event.get("data", ""))
                  failed = observation.startswith(("stderr:", "Error:"))
                  thread_safe_Callback(
                    {
                      "type": "observation",
                      "tool": active_tool,
                      "result": {
                        "exit_code": 1 if failed else 0,
                        "stdout": "" if failed else observation,
                        "stderr": observation if failed else "",
                      },
                    }
                  )
                elif event_type == "finish":
                  thread_safe_Callback(
                    {
                      "type": "finish",
                      "final_answer": engine_event.get("result", "Task completed."),
                    }
                  )

            async def run_engine() -> None:
                try:
                  await asyncio.to_thread(run_engine_sync)
                except Exception as exc:
                    thread_safe_Callback({"type": "error", "message": str(exc)})
                finally:
                    thread_safe_Callback({"type": "_stream_done"})

            worker_task = asyncio.create_task(run_engine())

            while True:
                ev = await event_queue.get()
                if ev.get("type") == "_stream_done":
                    break
                await websocket.send_json(ev)

            await worker_task

    except WebSocketDisconnect:
        pass


# ============================================================================
# 4. SERVER ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    ensure_ollama_daemon()
    threading.Timer(1.2, lambda: webbrowser.open(f"http://{SERVER_HOST}:{SERVER_PORT}")).start()
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT, reload=False)