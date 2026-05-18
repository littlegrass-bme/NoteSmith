import sys
import io
import json
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

with open("config.js", "r", encoding="utf-8") as f:
    js_text = f.read()

api_base_url = re.search(r'apiBaseUrl:\s*"([^"]+)"', js_text).group(1)
api_key = re.search(r'apiKey:\s*"([^"]+)"', js_text).group(1)

print(f"API Base URL: {api_base_url}")
print(f"API Key: {api_key[:8]}...{api_key[-4:]}")
print()

url = f"{api_base_url}/models"
try:
    resp = requests.get(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }, timeout=30)
    print(f"HTTP Status: {resp.status_code}")
    data = resp.json()
    models = data.get("data", data.get("models", []))
    if isinstance(data, list):
        models = data
    print(f"OK! Found {len(models)} models:")
    for m in models[:30]:
        name = m.get("id", m.get("name", str(m)))
        print(f"  - {name}")
    if len(models) > 30:
        print(f"  ... and {len(models) - 30} more")
except Exception as e:
    print(f"FAILED: {e}")
