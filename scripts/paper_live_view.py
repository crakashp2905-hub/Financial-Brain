"""Serve only the local paper viewer. No database access or order endpoints."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from financial_brain.paper.live_view import publish, view_path

LEDGER = ROOT / "data" / "paper_live.duckdb"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            body = view_path(LEDGER).read_bytes()
        except OSError:
            self.send_error(503, "Paper viewer not ready")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if "--open" in sys.argv:
        import subprocess
        import urllib.request
        import webbrowser
        import time
        url = "http://127.0.0.1:8776/"
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if b"Finance-Brain" not in response.read():
                    raise SystemExit("Port 8776 is in use by another service.")
        except OSError:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve())],
                             cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _attempt in range(30):
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if b"Finance-Brain" in response.read():
                            break
                except OSError:
                    time.sleep(0.2)
            else:
                raise SystemExit("Viewer could not start on port 8776.")
        webbrowser.open(url)
        raise SystemExit(0)
    if not view_path(LEDGER).exists():
        publish(LEDGER)
    server = HTTPServer(("127.0.0.1", 8776), Handler)
    print("Paper viewer: http://127.0.0.1:8776", flush=True)
    server.serve_forever()
