from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from freeflow_solver import (
    decode_image_bytes,
    encode_png,
    parse_grid_shape,
    read_puzzle,
    render_debug,
    render_solution,
    solve_puzzle,
)


ROOT = Path(__file__).resolve().parent
SOLVER_LOCK = threading.Lock()


INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Free Flow Solver</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #151719;
      --panel: #202428;
      --panel-2: #262b30;
      --line: #3b4249;
      --text: #f2f4f5;
      --muted: #aeb7bf;
      --accent: #40d26d;
      --accent-2: #49a6ff;
      --danger: #ff6262;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input { font: inherit; }
    .app {
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }
    header {
      height: 58px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 0 18px;
      border-bottom: 1px solid var(--line);
      background: #111315;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .status {
      min-height: 22px;
      color: var(--muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: right;
    }
    .status.busy {
      color: var(--text);
    }
    .status.busy::before {
      content: "";
      display: inline-block;
      width: 12px;
      height: 12px;
      margin-right: 8px;
      border: 2px solid #5d6871;
      border-top-color: var(--accent);
      border-radius: 50%;
      vertical-align: -2px;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    main {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      min-height: 0;
    }
    aside {
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .dropzone {
      min-height: 190px;
      border: 1px dashed #66717b;
      border-radius: 8px;
      background: #191d20;
      display: grid;
      place-items: center;
      text-align: center;
      padding: 18px;
      color: var(--muted);
      cursor: pointer;
    }
    .dropzone strong {
      display: block;
      color: var(--text);
      font-size: 16px;
      margin-bottom: 6px;
    }
    .dropzone.drag {
      border-color: var(--accent);
      background: #16251b;
    }
    .controls {
      display: grid;
      gap: 12px;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
    }
    input[type="number"] {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #121416;
      color: var(--text);
      padding: 0 10px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
    }
    .actions {
      display: grid;
      grid-template-columns: 1fr 44px;
      gap: 10px;
    }
    button {
      height: 40px;
      border: 1px solid transparent;
      border-radius: 6px;
      background: var(--accent);
      color: #071009;
      font-weight: 700;
      cursor: pointer;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }
    button.busy {
      position: relative;
      color: transparent;
    }
    button.busy::after {
      content: "";
      position: absolute;
      left: 50%;
      top: 50%;
      width: 16px;
      height: 16px;
      margin-left: -8px;
      margin-top: -8px;
      border: 2px solid rgba(7, 16, 9, 0.3);
      border-top-color: #071009;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }
    .viewer.solving .pane:last-child .image-stage::after {
      content: "Solving";
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0;
    }
    .icon-button {
      background: var(--panel-2);
      color: var(--text);
      border-color: var(--line);
      font-size: 18px;
    }
    .meta {
      display: grid;
      gap: 6px;
      padding-top: 8px;
      color: var(--muted);
      border-top: 1px solid var(--line);
    }
    .workspace {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      background: #101214;
    }
    .tabs {
      height: 48px;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 0 14px;
      border-bottom: 1px solid var(--line);
      background: #151719;
    }
    .tab {
      width: auto;
      height: 32px;
      padding: 0 12px;
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--line);
      font-weight: 600;
    }
    .tab.active {
      background: var(--panel-2);
      color: var(--text);
      border-color: #65717b;
    }
    .viewer {
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      background: var(--line);
    }
    .pane {
      min-width: 0;
      min-height: 0;
      background: #0c0e10;
      display: grid;
      grid-template-rows: 34px 1fr;
    }
    .pane-title {
      display: flex;
      align-items: center;
      padding: 0 12px;
      color: var(--muted);
      background: #151719;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      font-weight: 650;
    }
    .image-stage {
      min-height: 0;
      display: grid;
      place-items: center;
      overflow: auto;
      padding: 16px;
    }
    .image-stage img {
      max-width: min(100%, 980px);
      max-height: calc(100vh - 155px);
      object-fit: contain;
      image-rendering: auto;
    }
    .placeholder {
      color: #7f8991;
      text-align: center;
      padding: 20px;
    }
    .error { color: var(--danger); }
    .hidden { display: none; }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .viewer { grid-template-columns: 1fr; }
      .image-stage img { max-height: 70vh; }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>Free Flow Solver</h1>
      <div id="status" class="status">이미지를 붙여넣거나 선택하세요.</div>
    </header>
    <main>
      <aside>
        <input id="fileInput" class="hidden" type="file" accept="image/*">
        <div id="dropzone" class="dropzone" tabindex="0">
          <div>
            <strong>Paste / Drop / Click</strong>
            Ctrl+V, 드래그 앤 드롭, 파일 선택
          </div>
        </div>
        <div class="controls">
          <div class="row">
            <label>Grid size
              <input id="gridSize" type="text" inputmode="numeric" placeholder="auto">
            </label>
            <label>Max paths
              <input id="maxPaths" type="number" min="100" step="100" value="20000">
            </label>
          </div>
          <label>Min dot fill
            <input id="minDotFill" type="number" min="0.01" max="0.5" step="0.01" value="0.08">
          </label>
          <label class="check">
            <input id="showDebug" type="checkbox">
            Debug overlay
          </label>
          <div class="actions">
            <button id="solveBtn" disabled>풀기</button>
            <button id="clearBtn" class="icon-button" title="Clear" aria-label="Clear">×</button>
          </div>
        </div>
        <div class="meta">
          <div id="fileMeta">No image loaded</div>
          <div id="resultMeta">No result</div>
        </div>
      </aside>
      <section class="workspace">
        <div class="tabs">
          <button id="solvedTab" class="tab active">Solved</button>
          <button id="debugTab" class="tab">Debug</button>
        </div>
        <div id="viewer" class="viewer">
          <div class="pane">
            <div class="pane-title">Input</div>
            <div class="image-stage"><div id="inputEmpty" class="placeholder">캡처 이미지를 여기에 넣으세요.</div><img id="inputPreview" class="hidden" alt="Input image"></div>
          </div>
          <div class="pane">
            <div id="outputTitle" class="pane-title">Solved</div>
            <div class="image-stage"><div id="outputEmpty" class="placeholder">풀이 결과가 여기에 표시됩니다.</div><img id="outputPreview" class="hidden" alt="Solved image"></div>
          </div>
        </div>
      </section>
    </main>
  </div>
  <script>
    const state = { file: null, solvedUrl: "", debugUrl: "", active: "solved" };
    const $ = (id) => document.getElementById(id);
    const status = $("status");
    const fileInput = $("fileInput");
    const dropzone = $("dropzone");
    const solveBtn = $("solveBtn");
    const inputPreview = $("inputPreview");
    const outputPreview = $("outputPreview");
    const inputEmpty = $("inputEmpty");
    const outputEmpty = $("outputEmpty");
    const viewer = $("viewer");
    const fileMeta = $("fileMeta");
    const resultMeta = $("resultMeta");

    function setStatus(text, isError = false, isBusy = false) {
      status.textContent = text;
      status.classList.toggle("error", isError);
      status.classList.toggle("busy", isBusy);
    }

    function setSolving(isSolving) {
      solveBtn.disabled = isSolving || !state.file;
      solveBtn.classList.toggle("busy", isSolving);
      viewer.classList.toggle("solving", isSolving);
    }

    function setImage(img, empty, url) {
      img.src = url || "";
      img.classList.toggle("hidden", !url);
      empty.classList.toggle("hidden", !!url);
    }

    function setFile(file) {
      if (!file || !file.type.startsWith("image/")) {
        setStatus("이미지 파일만 사용할 수 있습니다.", true);
        return;
      }
      if (state.inputUrl) URL.revokeObjectURL(state.inputUrl);
      state.file = file;
      state.inputUrl = URL.createObjectURL(file);
      state.solvedUrl = "";
      state.debugUrl = "";
      setImage(inputPreview, inputEmpty, state.inputUrl);
      setImage(outputPreview, outputEmpty, "");
      fileMeta.textContent = `${file.name || "pasted image"} · ${Math.round(file.size / 1024)} KB`;
      resultMeta.textContent = "Ready";
      solveBtn.disabled = false;
      setStatus("이미지가 로드됐습니다.");
    }

    async function solve() {
      if (!state.file) return;
      setSolving(true);
      setStatus("풀이 중...", false, true);
      resultMeta.textContent = "Solving";
      const data = new FormData();
      data.append("image", state.file, state.file.name || "capture.png");
      data.append("grid_size", $("gridSize").value.trim());
      data.append("max_paths", $("maxPaths").value.trim() || "20000");
      data.append("min_dot_fill", $("minDotFill").value.trim() || "0.08");

      try {
        const response = await fetch("/solve", { method: "POST", body: data });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
          if (payload.debug) {
            state.debugUrl = `data:image/png;base64,${payload.debug}`;
            state.solvedUrl = "";
            resultMeta.textContent = payload.rows && payload.cols
              ? `${payload.cols}x${payload.rows} · ${payload.colors} colors`
              : "Failed";
            showOutput("debug");
          }
          throw new Error(payload.error || `HTTP ${response.status}`);
        }
        state.solvedUrl = `data:image/png;base64,${payload.solved}`;
        state.debugUrl = `data:image/png;base64,${payload.debug}`;
        resultMeta.textContent = `${payload.cols}x${payload.rows} · ${payload.colors} colors`;
        showOutput($("showDebug").checked ? "debug" : "solved");
        setStatus("완료");
      } catch (error) {
        setImage(outputPreview, outputEmpty, "");
        resultMeta.textContent = "Failed";
        setStatus(error.message, true);
      } finally {
        setSolving(false);
      }
    }

    function showOutput(kind) {
      state.active = kind;
      $("solvedTab").classList.toggle("active", kind === "solved");
      $("debugTab").classList.toggle("active", kind === "debug");
      $("outputTitle").textContent = kind === "debug" ? "Debug" : "Solved";
      const url = kind === "debug" ? state.debugUrl : state.solvedUrl;
      setImage(outputPreview, outputEmpty, url);
    }

    function clearAll() {
      state.file = null;
      state.solvedUrl = "";
      state.debugUrl = "";
      if (state.inputUrl) URL.revokeObjectURL(state.inputUrl);
      state.inputUrl = "";
      fileInput.value = "";
      setImage(inputPreview, inputEmpty, "");
      setImage(outputPreview, outputEmpty, "");
      fileMeta.textContent = "No image loaded";
      resultMeta.textContent = "No result";
      solveBtn.disabled = true;
      setStatus("이미지를 붙여넣거나 선택하세요.");
    }

    fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") fileInput.click();
    });
    dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropzone.classList.add("drag");
    });
    dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
    dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      dropzone.classList.remove("drag");
      setFile(event.dataTransfer.files[0]);
    });
    window.addEventListener("paste", (event) => {
      const item = [...event.clipboardData.items].find((entry) => entry.type.startsWith("image/"));
      if (item) setFile(item.getAsFile());
    });
    solveBtn.addEventListener("click", solve);
    $("clearBtn").addEventListener("click", clearAll);
    $("solvedTab").addEventListener("click", () => showOutput("solved"));
    $("debugTab").addEventListener("click", () => showOutput("debug"));
    $("showDebug").addEventListener("change", (event) => {
      if (state.solvedUrl) showOutput(event.target.checked ? "debug" : "solved");
    });
  </script>
</body>
</html>
"""


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "FreeFlowSolver/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/solve":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        try:
            content_type = self.headers.get("content-type", "")
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            fields, files = parse_multipart(body, content_type)
            image_bytes = files.get("image")
            if not image_bytes:
                raise ValueError("No image was uploaded.")

            grid_shape = parse_grid_shape(fields.get("grid_size"))
            max_paths = parse_optional_int(fields.get("max_paths")) or 20000
            min_dot_fill = parse_optional_float(fields.get("min_dot_fill")) or 0.08
            image = decode_image_bytes(image_bytes)
            puzzle = read_puzzle(image, grid_shape, min_dot_fill)
            debug = render_debug(puzzle, puzzle.colors)
            with SOLVER_LOCK:
                solved = solve_with_timeout(puzzle, max_paths=max_paths, timeout_seconds=25)
            payload = {
                "ok": True,
                "rows": puzzle.rows,
                "cols": puzzle.cols,
                "colors": len(puzzle.colors),
                "solved": base64.b64encode(encode_png(solved)).decode("ascii"),
                "debug": base64.b64encode(encode_png(debug)).decode("ascii"),
            }
            self.send_json(payload)
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
            if "puzzle" in locals() and "debug" in locals():
                payload.update(
                    {
                        "rows": puzzle.rows,
                        "cols": puzzle.cols,
                        "colors": len(puzzle.colors),
                        "debug": base64.b64encode(encode_png(debug)).decode("ascii"),
                    }
                )
            self.send_json(payload, HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return int(value)


def parse_optional_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def solve_with_timeout(puzzle, max_paths: int, timeout_seconds: int):
    return render_solution(puzzle, solve_puzzle(puzzle, max_paths))


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, bytes]]:
    header, params = parse_header(content_type)
    if header != "multipart/form-data" or "boundary" not in params:
        raise ValueError("Expected multipart/form-data request.")
    boundary = ("--" + params["boundary"]).encode("utf-8")
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}

    for raw_part in body.split(boundary):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = parse_part_headers(head)
        disposition, disposition_params = parse_header(headers.get("content-disposition", ""))
        if disposition != "form-data" or "name" not in disposition_params:
            continue
        name = disposition_params["name"]
        if "filename" in disposition_params:
            files[name] = content
        else:
            fields[name] = content.decode("utf-8", errors="replace")
    return fields, files


def parse_part_headers(raw: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.decode("iso-8859-1").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def parse_header(value: str) -> tuple[str, dict[str, str]]:
    parts = [part.strip() for part in value.split(";")]
    main = parts[0].lower() if parts else ""
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        params[key.strip().lower()] = raw_value.strip().strip('"')
    return main, params


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Free Flow Solver web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mimetypes.init()
    server = HTTPServer((args.host, args.port), RequestHandler)
    print(f"Free Flow Solver UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
