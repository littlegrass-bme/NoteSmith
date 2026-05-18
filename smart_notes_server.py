import http.server
import json
import mimetypes
import re
import socketserver
import subprocess
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.js"
DEFAULT_PORT = 8000


def load_config():
    text = CONFIG_FILE.read_text(encoding="utf-8")
    base_match = re.search(r'apiBaseUrl:\s*"([^"]+)"', text)
    key_match = re.search(r'apiKey:\s*"([^"]+)"', text)
    if not base_match or not key_match:
        raise RuntimeError("config.js must define apiBaseUrl and apiKey")
    return {
        "api_base_url": base_match.group(1).rstrip("/"),
        "api_key": key_match.group(1),
    }


CONFIG = load_config()


class SmartNotesHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = "/smart_notes_v28.html"
        return str((ROOT / path.lstrip("/")).resolve())

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/v1/"):
            self.proxy_api()
            return
        if self.path.split("?", 1)[0] == "/config.js":
            self.serve_browser_config()
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/v1/"):
            self.proxy_api()
            return
        self.send_error(404)

    def do_OPTIONS(self):
        if self.path.startswith("/v1/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
            self.end_headers()
            return
        self.send_error(404)

    def serve_browser_config(self):
        body = (
            "const SMART_NOTES_CONFIG = {\n"
            '    apiBaseUrl: "/v1",\n'
            '    apiKey: ""\n'
            "};\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def proxy_api(self):
        upstream_path = self.path[len("/v1") :]
        url = f"{CONFIG['api_base_url']}{upstream_path}"
        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b""

        headers = {
            "Authorization": f"Bearer {CONFIG['api_key']}",
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "Accept": self.headers.get("Accept", "application/json"),
        }
        try:
            response = requests.request(self.command, url, data=body, headers=headers, timeout=120)
            payload = response.content
            self.send_response(response.status_code)
            self.copy_response_headers(response.headers, len(payload))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            self.proxy_with_curl(url, body, headers, exc)

    def proxy_with_curl(self, url, body, headers, original_error):
        marker = b"\n__SMART_NOTES_STATUS__:"
        args = [
            "curl.exe",
            "-sS",
            "-X",
            self.command,
            url,
            "-H",
            f"Authorization: {headers['Authorization']}",
            "-H",
            f"Content-Type: {headers['Content-Type']}",
            "-H",
            f"Accept: {headers['Accept']}",
            "-w",
            marker.decode("ascii") + "%{http_code}",
        ]
        if body is not None:
            args.extend(["--data-binary", "@-"])

        try:
            completed = subprocess.run(
                args,
                input=body,
                capture_output=True,
                check=False,
                timeout=120,
            )
            output = completed.stdout
            if marker not in output:
                raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
            payload, status_text = output.rsplit(marker, 1)
            status = int(status_text.strip() or b"502")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps({
                "error": {
                    "message": f"{original_error}; curl fallback failed: {exc}"
                }
            }).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def copy_response_headers(self, headers, content_length):
        content_type = headers.get("Content-Type") or "application/json; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Access-Control-Allow-Origin", "*")


def run():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    mimetypes.add_type("application/javascript; charset=utf-8", ".js")
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), SmartNotesHandler) as httpd:
        print(f"Smart Notes server: http://127.0.0.1:{port}/")
        print(f"Proxying /v1 to {CONFIG['api_base_url']}")
        httpd.serve_forever()


if __name__ == "__main__":
    run()
