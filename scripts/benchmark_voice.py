"""Run assigned-operator in-network voice measurements; no member token on this host.

Configuration comes from the azd environment: VOICE_DEMO_ORIGIN (gateway origin) and VOICE_DEMO_CLIENT_ID
(the presenter app registration created by scripts/configure_voice_demo.py), or the matching flags.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from uuid import UUID

GATEWAY_HOST = re.compile(r"apim-gg-[a-z0-9]+\.azure-api\.net")


def validated_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or not GATEWAY_HOST.fullmatch(parsed.hostname)
            or parsed.netloc != parsed.hostname or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("origin must be the deployment's https://apim-gg-<suffix>.azure-api.net gateway")
    return f"https://{parsed.hostname}"


def run(output: Path, browser_path: str, model: str, origin: str, client_id: str) -> None:
    from playwright.sync_api import Error as BrowserError, sync_playwright

    origin = validated_origin(origin)
    scope = f"api://{UUID(client_id)}/VoiceDemo.Access"
    auth = json.loads(subprocess.check_output([
        "az", "account", "get-access-token", "--scope", scope, "-o", "json",
    ], text=True))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True,
                                             args=["--no-sandbox"])
        try:
            context = browser.new_context()
            response = context.request.post(origin + "/voice-demo/authorize", max_redirects=0,
                                             headers={"Origin": origin,
                                                      "Authorization": "Bearer " + auth["accessToken"]})
            auth.clear()
            if response.status != 200:
                raise RuntimeError(f"presenter authorization HTTP {response.status}")
            page = context.new_page()
            page.goto(origin + "/voice-demo/", wait_until="networkidle")
            page.evaluate("""model => {
              window.benchmarkResult=null;window.benchmarkFailed=false;window.benchmarkProgress=0;
              const socket=new WebSocket(location.origin.replace('https:','wss:')+'/voice-demo-stream');
              socket.onopen=()=>socket.send(JSON.stringify({mode:'benchmark',benchmark_model:model}));
              socket.onmessage=event=>{const message=JSON.parse(event.data);
                if(message.kind==='benchmark_complete')window.benchmarkResult=message;
                if(message.kind==='benchmark_progress')window.benchmarkProgress=message.turn;
                if(message.kind==='error'){window.benchmarkFailed=true;
                  window.benchmarkError={type:message.error_type,location:message.location};}};
              socket.onclose=event=>{window.benchmarkClose=event.code;
                if(!window.benchmarkResult)window.benchmarkFailed=true;};
            }""", model)
            page.wait_for_function("() => window.benchmarkResult || window.benchmarkFailed", timeout=860000)
            result = page.evaluate("window.benchmarkResult")
            if not result:
                state = page.evaluate("({turns:window.benchmarkProgress,close:window.benchmarkClose,error:window.benchmarkError})")
                raise RuntimeError(f"benchmark failed: {state}; inspect content-free runtime diagnostics")
            output.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result["summary"], indent=2))
        except BrowserError:
            raise RuntimeError("benchmark browser transport failed; credentials not exported") from None
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", default="/snap/bin/chromium")
    parser.add_argument("--model", choices=("gpt-counsellor", "gpt-compact"), default="gpt-counsellor")
    parser.add_argument("--origin", default=os.environ.get("VOICE_DEMO_ORIGIN", ""))
    parser.add_argument("--client-id", default=os.environ.get("VOICE_DEMO_CLIENT_ID", ""))
    args = parser.parse_args()
    if not args.origin or not args.client_id:
        parser.error("--origin/VOICE_DEMO_ORIGIN and --client-id/VOICE_DEMO_CLIENT_ID are required")
    run(args.output, args.browser, args.model, args.origin, args.client_id)
