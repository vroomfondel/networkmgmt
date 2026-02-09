#!/usr/bin/env python3
"""Local Mermaid diagram viewer with pan & zoom.

Extracts all ```mermaid blocks from a markdown file, renders them
in the browser with pan/zoom support, and auto-reloads on file changes.

Usage:
    python scripts/mermaid_viewer.py topo_101.md
    python scripts/mermaid_viewer.py topo_101.md --port 8123
"""

import argparse
import glob
import html
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser

# Search paths for local mermaid dist directory (mermaid-cli, global npm, etc.)
MERMAID_DIST_SEARCH = [
    "/usr/local/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/mermaid/dist",
    "/usr/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/mermaid/dist",
    "/usr/local/lib/node_modules/mermaid/dist",
    "/usr/lib/node_modules/mermaid/dist",
]

# Search paths for ELK layout dist directory
ELK_DIST_SEARCH = [
    "/usr/local/lib/node_modules/@mermaid-js/layout-elk/dist",
    "/usr/lib/node_modules/@mermaid-js/layout-elk/dist",
    "/usr/local/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/@mermaid-js/layout-elk/dist",
    "/usr/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/@mermaid-js/layout-elk/dist",
]


def _find_dist_dir(search_paths: list[str], marker_file: str, npm_suffixes: list[str] | None = None) -> str | None:
    """Find a dist directory containing a marker file."""
    for p in search_paths:
        if os.path.isfile(os.path.join(p, marker_file)):
            return p
    # Try npm root -g
    try:
        npm = shutil.which("npm")
        if npm and npm_suffixes:
            root = subprocess.check_output([npm, "root", "-g"], text=True, timeout=5).strip()
            for suffix in npm_suffixes:
                candidate = os.path.join(root, suffix)
                if os.path.isfile(os.path.join(candidate, marker_file)):
                    return candidate
    except Exception:
        pass
    return None


def find_mermaid_dist() -> str | None:
    """Find mermaid dist directory on the local system."""
    result = _find_dist_dir(
        MERMAID_DIST_SEARCH,
        "mermaid.esm.min.mjs",
        [
            "@mermaid-js/mermaid-cli/node_modules/mermaid/dist",
            "mermaid/dist",
        ],
    )
    if result:
        return result
    # Fallback: find mermaid.min.js for non-ESM mode
    return _find_dist_dir(
        MERMAID_DIST_SEARCH,
        "mermaid.min.js",
        [
            "@mermaid-js/mermaid-cli/node_modules/mermaid/dist",
            "mermaid/dist",
        ],
    )


def find_elk_dist() -> str | None:
    """Find @mermaid-js/layout-elk dist directory."""
    return _find_dist_dir(
        ELK_DIST_SEARCH,
        "mermaid-layout-elk.esm.min.mjs",
        [
            "@mermaid-js/layout-elk/dist",
            "@mermaid-js/mermaid-cli/node_modules/@mermaid-js/layout-elk/dist",
        ],
    )


def extract_mermaid_blocks(filepath: str) -> list[dict]:
    """Extract all ```mermaid code blocks from a markdown file."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = []
    pattern = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
    for i, match in enumerate(pattern.finditer(content)):
        code = match.group(1).rstrip("\n")
        first_line = code.strip().split("\n")[0].strip().lower()
        diagram_type = first_line.split()[0] if first_line else "diagram"
        blocks.append(
            {
                "index": i,
                "code": code,
                "type": diagram_type,
            }
        )
    return blocks


def build_html(blocks: list[dict], filepath: str, elk_available: bool = False) -> str:
    """Build the viewer HTML page."""
    diagrams_json = json.dumps([b["code"] for b in blocks])
    tab_labels_json = json.dumps([f"Diagram {b['index']+1} ({b['type']})" for b in blocks])
    filename = os.path.basename(filepath)

    # NOTE: We use <BRACE> / <CBRACE> placeholders to avoid f-string issues,
    # then replace them at the end. This keeps the JS clean and readable.
    template = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Mermaid Viewer - __FILENAME__</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .toolbar {
    background: #16213e;
    padding: 8px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid #0f3460;
    flex-shrink: 0;
    flex-wrap: wrap;
  }
  .toolbar h1 {
    font-size: 14px;
    font-weight: 600;
    color: #e94560;
    white-space: nowrap;
  }
  .tabs {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  .tab {
    padding: 4px 12px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    background: #0f3460;
    color: #a0a0c0;
    border: 1px solid transparent;
    transition: all 0.15s;
  }
  .tab:hover { background: #1a4a7a; color: #fff; }
  .tab.active { background: #e94560; color: #fff; border-color: #e94560; }
  .controls {
    margin-left: auto;
    display: flex;
    gap: 6px;
    align-items: center;
    flex-shrink: 0;
  }
  .controls button {
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid #0f3460;
    background: #16213e;
    color: #e0e0e0;
    cursor: pointer;
    font-size: 13px;
  }
  .controls button:hover { background: #0f3460; }
  .zoom-label {
    font-size: 12px;
    color: #a0a0c0;
    min-width: 45px;
    text-align: center;
  }
  .viewport {
    flex: 1;
    overflow: hidden;
    position: relative;
    cursor: grab;
  }
  .viewport.grabbing { cursor: grabbing; }
  .canvas {
    position: absolute;
    transform-origin: 0 0;
    padding: 40px;
  }
  .mermaid-container {
    background: #fff;
    border-radius: 8px;
    padding: 24px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
    display: inline-block;
    min-width: 200px;
    min-height: 100px;
  }
  .mermaid-container svg {
    max-width: none !important;
    height: auto !important;
  }
  #loading {
    color: #a0a0c0;
    font-size: 16px;
    padding: 40px;
  }
  #errorBox {
    color: #ff6b6b;
    background: #2d1b1b;
    border: 1px solid #ff6b6b;
    border-radius: 4px;
    padding: 16px;
    margin: 8px;
    font-family: monospace;
    font-size: 13px;
    white-space: pre-wrap;
    display: none;
  }
  .status {
    position: fixed;
    bottom: 12px;
    right: 12px;
    font-size: 11px;
    color: #555;
    background: rgba(22,33,62,0.9);
    padding: 4px 8px;
    border-radius: 4px;
  }
  .status.connected { color: #4caf50; }
</style>
</head>
<body>

<div class="toolbar">
  <h1>__FILENAME__</h1>
  <div class="tabs" id="tabs"></div>
  <div class="controls">
    <button onclick="zoomTo(1)" title="Reset zoom">Reset</button>
    <button onclick="zoomBy(-0.2)" title="Zoom out">&minus;</button>
    <span class="zoom-label" id="zoomLabel">100%</span>
    <button onclick="zoomBy(0.2)" title="Zoom in">+</button>
    <button onclick="fitToScreen()" title="Fit to screen">Fit</button>
    <button onclick="downloadSVG()" title="Download SVG">SVG</button>
  </div>
</div>

<div id="errorBox"></div>

<div class="viewport" id="viewport">
  <div class="canvas" id="canvas">
    <div class="mermaid-container">
      <div id="loading">Loading mermaid.js...</div>
      <div id="mermaidTarget"></div>
    </div>
  </div>
</div>

<div class="status" id="status">starting...</div>

<script type="module">
import mermaid from '/mermaid-dist/mermaid.esm.min.mjs';
__ELK_IMPORT__

var diagrams = __DIAGRAMS_JSON__;
var tabLabels = __TAB_LABELS_JSON__;
var currentIdx = 0;
var scale = 1, panX = 0, panY = 0;
var dragging = false, dragStartX = 0, dragStartY = 0, panStartX = 0, panStartY = 0;
var renderCounter = 0;

var viewport = document.getElementById('viewport');
var canvas = document.getElementById('canvas');
var loadingEl = document.getElementById('loading');
var errorBox = document.getElementById('errorBox');

function showError(msg) {
  errorBox.style.display = 'block';
  errorBox.textContent = msg;
  console.error('Mermaid Viewer Error:', msg);
}

loadingEl.textContent = 'Initializing mermaid...';

try {
  __ELK_REGISTER__
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    flowchart: { useMaxWidth: false, htmlLabels: true },
    maxTextSize: 500000
  });
} catch (e) {
  showError('mermaid.initialize() failed: ' + e.message);
}

  // --- Tabs ---
  var tabsEl = document.getElementById('tabs');
  function buildTabs() {
    tabsEl.innerHTML = '';
    tabLabels.forEach(function(label, i) {
      var t = document.createElement('div');
      t.className = 'tab' + (i === currentIdx ? ' active' : '');
      t.textContent = label;
      t.onclick = function() { switchDiagram(i); };
      tabsEl.appendChild(t);
    });
    tabsEl.style.display = tabLabels.length > 1 ? 'flex' : 'none';
  }

  function renderDiagram(idx) {
    var target = document.getElementById('mermaidTarget');
    loadingEl.style.display = 'block';
    loadingEl.textContent = 'Rendering diagram...';
    errorBox.style.display = 'none';
    target.innerHTML = '';

    renderCounter++;
    var renderId = 'mmd-' + renderCounter + '-' + Date.now();

    // Use a small delay so the "Rendering..." message shows
    setTimeout(function() {
      try {
        // mermaid v10 render() can be sync or async depending on version
        var result = mermaid.render(renderId, diagrams[idx]);

        if (result && typeof result.then === 'function') {
          // Promise-based (mermaid v10+)
          result.then(function(res) {
            loadingEl.style.display = 'none';
            target.innerHTML = res.svg;
            resetView();
          }).catch(function(e) {
            loadingEl.style.display = 'none';
            showError('Mermaid render error: ' + (e.message || e));
            // mermaid sometimes leaves error SVGs in the DOM
            var errSvg = document.getElementById('d' + renderId);
            if (errSvg) errSvg.remove();
          });
        } else if (result && result.svg) {
          // Object return
          loadingEl.style.display = 'none';
          target.innerHTML = result.svg;
          resetView();
        } else if (typeof result === 'string') {
          // Direct SVG string (older mermaid)
          loadingEl.style.display = 'none';
          target.innerHTML = result;
          resetView();
        }
      } catch (e) {
        loadingEl.style.display = 'none';
        showError('Mermaid render error: ' + (e.message || e));
      }
    }, 50);
  }

  function switchDiagram(idx) {
    currentIdx = idx;
    buildTabs();
    renderDiagram(idx);
  }
  window.switchDiagram = switchDiagram;

  // --- Pan & Zoom ---
  function applyTransform() {
    canvas.style.transform = 'translate(' + panX + 'px, ' + panY + 'px) scale(' + scale + ')';
    document.getElementById('zoomLabel').textContent = Math.round(scale * 100) + '%';
  }

  window.zoomBy = function(delta) {
    var rect = viewport.getBoundingClientRect();
    var cx = rect.width / 2;
    var cy = rect.height / 2;
    var newScale = Math.max(0.05, Math.min(10, scale + delta));
    var ratio = newScale / scale;
    panX = cx - ratio * (cx - panX);
    panY = cy - ratio * (cy - panY);
    scale = newScale;
    applyTransform();
  };

  window.zoomTo = function(s) {
    scale = s;
    panX = 0;
    panY = 0;
    applyTransform();
  };

  window.fitToScreen = function() {
    var svgEl = canvas.querySelector('svg');
    if (!svgEl) return;
    var vr = viewport.getBoundingClientRect();
    var sr = svgEl.getBoundingClientRect();
    var realW = sr.width / scale;
    var realH = sr.height / scale;
    var padding = 60;
    var fitScale = Math.min(
      (vr.width - padding) / realW,
      (vr.height - padding) / realH,
      3
    );
    scale = fitScale;
    panX = (vr.width - realW * scale) / 2;
    panY = (vr.height - realH * scale) / 2;
    applyTransform();
  };

  function resetView() {
    setTimeout(function() { window.fitToScreen(); }, 100);
  }

  // mouse wheel zoom
  viewport.addEventListener('wheel', function(e) {
    e.preventDefault();
    var rect = viewport.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;
    var delta = e.deltaY > 0 ? -0.1 : 0.1;
    var newScale = Math.max(0.05, Math.min(10, scale + delta * scale));
    var ratio = newScale / scale;
    panX = mx - ratio * (mx - panX);
    panY = my - ratio * (my - panY);
    scale = newScale;
    applyTransform();
  }, { passive: false });

  // drag to pan
  viewport.addEventListener('mousedown', function(e) {
    if (e.button !== 0) return;
    dragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    panStartX = panX;
    panStartY = panY;
    viewport.classList.add('grabbing');
  });
  window.addEventListener('mousemove', function(e) {
    if (!dragging) return;
    panX = panStartX + (e.clientX - dragStartX);
    panY = panStartY + (e.clientY - dragStartY);
    applyTransform();
  });
  window.addEventListener('mouseup', function() {
    dragging = false;
    viewport.classList.remove('grabbing');
  });

  // touch support
  var lastTouchDist = 0;
  var lastTouchCenter = null;
  viewport.addEventListener('touchstart', function(e) {
    if (e.touches.length === 1) {
      dragging = true;
      dragStartX = e.touches[0].clientX;
      dragStartY = e.touches[0].clientY;
      panStartX = panX;
      panStartY = panY;
    } else if (e.touches.length === 2) {
      dragging = false;
      var dx = e.touches[1].clientX - e.touches[0].clientX;
      var dy = e.touches[1].clientY - e.touches[0].clientY;
      lastTouchDist = Math.sqrt(dx*dx + dy*dy);
      lastTouchCenter = {
        x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
        y: (e.touches[0].clientY + e.touches[1].clientY) / 2
      };
    }
  }, { passive: false });
  viewport.addEventListener('touchmove', function(e) {
    e.preventDefault();
    if (e.touches.length === 1 && dragging) {
      panX = panStartX + (e.touches[0].clientX - dragStartX);
      panY = panStartY + (e.touches[0].clientY - dragStartY);
      applyTransform();
    } else if (e.touches.length === 2) {
      var dx = e.touches[1].clientX - e.touches[0].clientX;
      var dy = e.touches[1].clientY - e.touches[0].clientY;
      var dist = Math.sqrt(dx*dx + dy*dy);
      if (lastTouchDist) {
        var ratio = dist / lastTouchDist;
        var rect = viewport.getBoundingClientRect();
        var cx = lastTouchCenter.x - rect.left;
        var cy = lastTouchCenter.y - rect.top;
        var newScale = Math.max(0.05, Math.min(10, scale * ratio));
        var r = newScale / scale;
        panX = cx - r * (cx - panX);
        panY = cy - r * (cy - panY);
        scale = newScale;
        applyTransform();
      }
      lastTouchDist = dist;
    }
  }, { passive: false });
  viewport.addEventListener('touchend', function() { dragging = false; lastTouchDist = 0; });

  // keyboard shortcuts
  window.addEventListener('keydown', function(e) {
    if (e.key === '0') window.zoomTo(1);
    if (e.key === 'f' || e.key === 'F') window.fitToScreen();
    if (e.key === '+' || e.key === '=') window.zoomBy(0.2);
    if (e.key === '-') window.zoomBy(-0.2);
    if (e.key === 'ArrowLeft' && currentIdx > 0) switchDiagram(currentIdx - 1);
    if (e.key === 'ArrowRight' && currentIdx < diagrams.length - 1) switchDiagram(currentIdx + 1);
  });

  // SVG download
  window.downloadSVG = function() {
    var svgEl = canvas.querySelector('svg');
    if (!svgEl) return;
    var svgData = new XMLSerializer().serializeToString(svgEl);
    var blob = new Blob([svgData], { type: 'image/svg+xml' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'mermaid-diagram.svg';
    a.click();
  };

  // --- Auto-reload via polling ---
  var lastMtime = 0;
  function pollChanges() {
    fetch('/check').then(function(r) { return r.json(); }).then(function(data) {
      var statusEl = document.getElementById('status');
      if (lastMtime === 0) {
        lastMtime = data.mtime;
        statusEl.textContent = 'watching for changes';
        statusEl.className = 'status connected';
      } else if (data.mtime !== lastMtime) {
        lastMtime = data.mtime;
        statusEl.textContent = 'reloading...';
        location.reload();
      }
    }).catch(function() {});
  }
  setInterval(pollChanges, 1000);

  // --- Init ---
  document.getElementById('status').textContent = 'loading...';
  buildTabs();
  renderDiagram(0);
</script>
</body>
</html>"""

    template = template.replace("__FILENAME__", html.escape(filename))
    template = template.replace("__DIAGRAMS_JSON__", diagrams_json)
    template = template.replace("__TAB_LABELS_JSON__", tab_labels_json)

    if elk_available:
        template = template.replace(
            "__ELK_IMPORT__",
            "import elkLayouts from '/elk-dist/mermaid-layout-elk.esm.min.mjs';",
        )
        template = template.replace(
            "__ELK_REGISTER__",
            "mermaid.registerLayoutLoaders(elkLayouts);",
        )
    else:
        template = template.replace("__ELK_IMPORT__", "// ELK layout not available")
        template = template.replace("__ELK_REGISTER__", "")

    return template


class ViewerHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler serving the viewer and file-change check endpoint."""

    watched_file: str = ""
    mermaid_dist_path: str = ""
    elk_dist_path: str = ""
    elk_available: bool = False
    _file_cache: dict[str, bytes] = {}

    # Map file extensions to MIME types
    _MIME_TYPES = {
        ".mjs": "application/javascript",
        ".js": "application/javascript",
        ".json": "application/json",
        ".css": "text/css",
        ".wasm": "application/wasm",
    }

    def _serve_file_from_dist(self, url_prefix: str, dist_root: str) -> bool:
        """Serve a file from a dist directory. Returns True if served."""
        if not self.path.startswith(url_prefix):
            return False
        rel_path = self.path[len(url_prefix) :]
        # Security: prevent path traversal
        rel_path = os.path.normpath(rel_path)
        if rel_path.startswith("..") or os.path.isabs(rel_path):
            self.send_response(403)
            self.end_headers()
            return True

        full_path = os.path.join(dist_root, rel_path)
        if not os.path.isfile(full_path):
            self.send_response(404)
            self.end_headers()
            return True

        # Read and cache file
        if full_path not in ViewerHandler._file_cache:
            with open(full_path, "rb") as f:
                ViewerHandler._file_cache[full_path] = f.read()
        data = ViewerHandler._file_cache[full_path]

        ext = os.path.splitext(full_path)[1].lower()
        content_type = self._MIME_TYPES.get(ext, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)
        return True

    def do_GET(self) -> None:
        if self.path == "/check":
            try:
                mtime = os.path.getmtime(self.watched_file)
            except OSError:
                mtime = 0
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"mtime": mtime}).encode())
        elif self._serve_file_from_dist("/mermaid-dist/", self.mermaid_dist_path):
            pass
        elif self.elk_dist_path and self._serve_file_from_dist("/elk-dist/", self.elk_dist_path):
            pass
        elif self.path == "/" or self.path == "/index.html":
            blocks = extract_mermaid_blocks(self.watched_file)
            content = build_html(blocks, self.watched_file, elk_available=self.elk_available)
            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Mermaid diagram viewer")
    parser.add_argument("file", help="Markdown file containing ```mermaid blocks")
    parser.add_argument("--port", type=int, default=8199, help="Port (default: 8199)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    filepath = os.path.abspath(args.file)
    if not os.path.isfile(filepath):
        print(f"Error: {filepath} not found", file=sys.stderr)
        sys.exit(1)

    blocks = extract_mermaid_blocks(filepath)
    if not blocks:
        print(f"Error: no ```mermaid blocks found in {filepath}", file=sys.stderr)
        sys.exit(1)

    mermaid_dist = find_mermaid_dist()
    if not mermaid_dist:
        print(
            "Error: mermaid dist directory not found locally.\n"
            "Install it with: npm install -g @mermaid-js/mermaid-cli",
            file=sys.stderr,
        )
        sys.exit(1)

    elk_dist = find_elk_dist()

    print(f"Found {len(blocks)} mermaid diagram(s) in {os.path.basename(filepath)}")
    print(f"Using mermaid from {mermaid_dist}")
    if elk_dist:
        print(f"ELK layout available from {elk_dist}")
    else:
        print("ELK layout not available (install: npm install -g @mermaid-js/layout-elk)")

    ViewerHandler.watched_file = filepath
    ViewerHandler.mermaid_dist_path = mermaid_dist
    ViewerHandler.elk_dist_path = elk_dist or ""
    ViewerHandler.elk_available = elk_dist is not None

    server = http.server.HTTPServer(("127.0.0.1", args.port), ViewerHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Serving at {url}")
    print("Shortcuts: scroll=zoom, drag=pan, F=fit, 0=reset, +/-=zoom, arrows=switch")
    print("Press Ctrl+C to stop")

    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
