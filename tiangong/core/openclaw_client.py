import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request


class OpenClawClient:
    def __init__(self, gateway_url: Optional[str] = None, token: Optional[str] = None,
                 session_key: Optional[str] = None, config_path: Optional[str] = None):
        cfg = self._load_config(config_path)
        gw = cfg.get("gateway", {}) if cfg else {}
        auth = gw.get("auth", {}) if gw else {}

        port = gw.get("port", 18789)
        host = "127.0.0.1"
        default_url = f"http://{host}:{port}"

        self.gateway_url = (
            gateway_url
            or os.environ.get("OPENCLAW_GATEWAY_URL")
            or default_url
        )
        self.token = (
            token
            or os.environ.get("OPENCLAW_GATEWAY_TOKEN")
            or auth.get("token")
        )
        self.session_key = session_key or os.environ.get("OPENCLAW_SESSION_KEY") or "main"

    def _load_config(self, config_path: Optional[str]) -> dict:
        path = Path(config_path or os.environ.get("OPENCLAW_CONFIG", "~/.openclaw/openclaw.json")).expanduser()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def invoke_tool(self, tool: str, args: Optional[dict] = None, action: Optional[str] = None,
                    session_key: Optional[str] = None) -> dict:
        if not self.token:
            raise RuntimeError("OPENCLAW_GATEWAY_TOKEN is missing.")
        url = f"{self.gateway_url.rstrip('/')}/tools/invoke"
        payload: Dict[str, Any] = {"tool": tool, "args": args or {}}
        if action:
            payload["action"] = action
        payload["sessionKey"] = session_key or self.session_key

        data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.token}")

        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    def sessions_list(self, **kwargs):
        return self.invoke_tool("sessions_list", args=kwargs)

    def sessions_send(self, session_key: str, message: str, timeout_seconds: Optional[int] = None):
        args: Dict[str, Any] = {"sessionKey": session_key, "message": message}
        if timeout_seconds is not None:
            args["timeoutSeconds"] = timeout_seconds
        return self.invoke_tool("sessions_send", args=args)

    def sessions_spawn(self, task: str, label: Optional[str] = None, agent_id: Optional[str] = None,
                       model: Optional[str] = None, run_timeout_seconds: Optional[int] = None,
                       cleanup: Optional[str] = None):
        args: Dict[str, Any] = {"task": task}
        if label:
            args["label"] = label
        if agent_id:
            args["agentId"] = agent_id
        if model:
            args["model"] = model
        if run_timeout_seconds is not None:
            args["runTimeoutSeconds"] = run_timeout_seconds
        if cleanup:
            args["cleanup"] = cleanup
        return self.invoke_tool("sessions_spawn", args=args)
