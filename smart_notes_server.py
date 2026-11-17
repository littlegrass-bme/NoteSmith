import http.server
import json
import mimetypes
import os
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
INDEX_CANDIDATES = ("smart_notes.html", "smart_notes_v28.html")


def pick_index_page() -> str:
    for name in INDEX_CANDIDATES:
        if (ROOT / name).is_file():
            return f"/{name}"
    return f"/{INDEX_CANDIDATES[0]}"


INDEX_PAGE = pick_index_page()


def _read_pairs(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    pairs = {}
    for key in ("apiBaseUrl", "apiKey", "model"):
        m = re.search(rf'{key}:\s*"([^"]*)"', text)
        if m:
            pairs[key] = m.group(1).strip()
    return pairs


def load_config():
    base = _read_pairs(CONFIG_FILE)
    # config.local.js 覆盖 config.js，且不进 git。
    local = _read_pairs(ROOT / "config.local.js")
    merged = {**base, **{k: v for k, v in local.items() if v}}

    if not merged.get("apiBaseUrl"):
        raise RuntimeError("config.js 里没有 apiBaseUrl")
    # 和 DSH 一样支持从环境变量取 Key，三者优先级：本地文件 > config.js > 环境变量。
    # 没配 Key 不算错误：服务照常起来，页面输入框留空，用户可以手填，
    # 也可以在 config.local.js 或环境变量里补上后重启。
    api_key = merged.get("apiKey") or os.environ.get("SHANGHAITECH_API_KEY", "").strip()
    return {
        "api_base_url": merged["apiBaseUrl"].rstrip("/"),
        "api_key": api_key,
        "model": merged.get("model") or "deepseek-v4-pro",
        "has_key": bool(api_key),
    }


CONFIG = load_config()
MODEL_ID = CONFIG["model"]


class SmartNotesHandler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            path = INDEX_PAGE
        return str((ROOT / path.lstrip("/")).resolve())

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] in ("/v1/models", "/v1/models/"):
            self.serve_model_list()
            return
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

    def _cross_origin_rejection(self):
        """判断这个 /config.js 请求是否来自别的网页，返回拒绝原因或 None。

        只放行本机同源的直接访问：页面自己用 <script src="config.js"> 加载，
        或用户在地址栏直接打开。别的站点试图用 script 标签/iframe 读取时拦掉。
        """
        host = (self.headers.get("Host") or "").strip().lower()
        # 1. Host 必须是本机名，挡住 DNS rebinding
        allowed_hosts = {"127.0.0.1", "localhost", "[::1]"}
        hostname = host.rsplit(":", 1)[0] if ":" in host and not host.endswith("]") else host
        if hostname not in allowed_hosts:
            return f"unexpected host {host!r}"

        # 2. 浏览器带的 Fetch 元数据：跨站请求直接拒
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site in {"cross-site", "same-site"}:
            return f"cross-origin fetch (Sec-Fetch-Site={site})"

        # 3. 若带 Origin，必须与本机同源
        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin.lower() != "null":
            ok = False
            for h in allowed_hosts:
                if origin.lower() in (f"http://{h}", f"https://{h}") or origin.lower().startswith(f"http://{h}:"):
                    ok = True
                    break
            if not ok:
                return f"cross-origin Origin={origin!r}"

        # 4. 若带 Referer，主机部分必须是本机
        referer = (self.headers.get("Referer") or "").strip()
        if referer:
            from urllib.parse import urlparse
            try:
                rhost = (urlparse(referer).hostname or "").lower()
            except Exception:
                rhost = ""
            if rhost and rhost not in allowed_hosts:
                return f"cross-origin Referer host={rhost!r}"

        return None

    def serve_browser_config(self):
        # 本服务只监听 127.0.0.1，页面自动填充需要拿到 Key。
        # Key 来自 config.local.js 或环境变量，只存在于内存和这次响应里：
        # 不写日志、不落盘、不进任何被 Git 跟踪的文件。
        # 仓库里的 config.js 始终 apiKey: ""，所以提交上去的永远是空模板。
        #
        # 跨来源保护：这段响应带真实 Key，必须挡住别的网页用 <script src> 偷读。
        # 1. 校验 Host 必须是本机，防 DNS rebinding 把别的域名解析到 127.0.0.1。
        # 2. Sec-Fetch-Site 为 cross-site 直接拒绝（浏览器会带这个头）。
        # 3. 校验 Origin/Referer 若存在必须同源，否则拒绝。
        # 4. 不发 CORS 放行头，并加 COOP/CORP，让跨源读取拿不到内容。
        deny_reason = self._cross_origin_rejection()
        if deny_reason:
            payload = json.dumps({
                "error": {"message": f"refused: {deny_reason}"}
            }).encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)
            return

        api_key = CONFIG["api_key"]
        body = (
            "const SMART_NOTES_CONFIG = {\n"
            '    apiBaseUrl: "/v1",\n'
            f"    apiKey: {json.dumps(api_key)}\n"
            "};\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 禁止 MIME 嗅探，禁止被跨源嵌入/读取
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        # 明确不发 Access-Control-Allow-Origin
        self.end_headers()
        self.wfile.write(body)

    def serve_model_list(self):
        # 学校网关不提供 GET /models，本地代理直接返回可用模型 id，
        # 免得前端每次都走失败分支。
        payload = json.dumps({
            "object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "owned_by": "shanghaitech"}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def proxy_api(self):
        upstream_path = self.path[len("/v1") :]
        url = f"{CONFIG['api_base_url']}{upstream_path}"
        body = None
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length) if length else b""

        if not CONFIG["has_key"]:
            payload = json.dumps({
                "error": {
                    "message": "本地服务没有读到 API Key：请在 config.local.js 填 apiKey，"
                               "或设置环境变量 SHANGHAITECH_API_KEY，然后重启服务。"
                }
            }).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
            return

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
    raw_port = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SMART_NOTES_PORT", "")
    port = int(raw_port) if str(raw_port).strip() else DEFAULT_PORT
    mimetypes.add_type("application/javascript; charset=utf-8", ".js")

    httpd = None
    for candidate in range(port, port + 20):
        try:
            httpd = socketserver.ThreadingTCPServer(("127.0.0.1", candidate), SmartNotesHandler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        print(f"{port} 到 {port + 19} 都被占用了，换个大一点的端口：python smart_notes_server.py 9000")
        sys.exit(1)

    with httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"Smart Notes server: {url}")
        print(f"Proxying /v1 to {CONFIG['api_base_url']}  (model={MODEL_ID})")
        if CONFIG["has_key"]:
            print("已从 config.local.js 读到 API Key，页面会自动填充。")
        else:
            print("未读到 API Key：页面输入框留空，请在 config.local.js 填 apiKey")
            print("或设置环境变量 SHANGHAITECH_API_KEY 后重启。")
        print("关闭窗口即停止服务。")
        # 双击快捷方式时自动打开浏览器；设置 SMART_NOTES_NO_BROWSER=1 可关闭。
        if os.environ.get("SMART_NOTES_NO_BROWSER", "").strip() != "1":
            try:
                import threading, webbrowser
                threading.Timer(1.0, lambda: webbrowser.open(url)).start()
            except Exception:
                pass
        httpd.serve_forever()


if __name__ == "__main__":
    run()
