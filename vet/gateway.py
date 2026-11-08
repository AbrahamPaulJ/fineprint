"""Thin stdlib client for the Otari gateway: chat, direct fetch and search, plus the headers we report on."""
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE = "https://api.otari.ai/api/v1"


class GatewayError(Exception):
    def __init__(self, status, code, detail):
        super().__init__(f"{status} {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


class GuardrailBlocked(GatewayError):
    """A guardrail in block mode refused the input before any model saw it."""


class BudgetExceeded(GatewayError):
    """A budget refused the request: the caller's own, or a ceiling above it."""


@dataclass
class Reply:
    content: str
    raw: dict
    headers: dict
    tool_calls: list = field(default_factory=list)  # otari_tool_calls: what the gateway ran for us

    @property
    def model(self):
        return self.headers.get("otari-model") or self.raw.get("model") or "?"

    @property
    def fallback(self):
        return self.headers.get("otari-fallback") == "true"

    @property
    def cost(self):
        try:
            return float(self.headers.get("otari-cost-usd") or 0)
        except ValueError:
            return 0.0


def load_env(path=".env"):
    """Read KEY=value lines into os.environ without overriding what is already set."""
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


class Gateway:
    def __init__(self, key=None, base=None, user=None, end_user_budget=None, timeout=240):
        self.key = key or os.environ.get("OTARI_API_KEY")
        self.base = (base or os.environ.get("OTARI_BASE") or DEFAULT_BASE).rstrip("/")
        self.user, self.end_user_budget, self.timeout = user, end_user_budget, timeout
        if not self.key:
            raise SystemExit("Set OTARI_API_KEY (in the environment or .env).")

    def _post(self, path, body, headers=None, method="POST"):
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "User-Agent": "fineprint/0.1"}
        if self.end_user_budget:
            h["Otari-End-User-Budget"] = self.end_user_budget
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace")), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            raise self._error(e) from None

    @staticmethod
    def _error(e):
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"detail": raw[:300]}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        detail = err.get("message") or body.get("detail") or body.get("message") or raw[:300]
        if isinstance(detail, dict):
            code = detail.get("code") or err.get("code")
            detail = detail.get("message") or json.dumps(detail)[:300]
        else:
            code = err.get("code") or body.get("code")
        code = code or e.headers.get("Otari-Error-Code") or str(e.code)
        if code == "guardrail_violation":
            return GuardrailBlocked(e.code, code, detail)
        if code in ("budget_exceeded", "insufficient_funds"):
            return BudgetExceeded(e.code, code, detail)
        return GatewayError(e.code, code, detail)

    def chat(self, model, messages, *, tools=None, guardrails=None, mcp_servers=None, response_format=None,
             max_tool_iterations=None, temperature=0, max_tokens=None, headers=None):
        body = {"model": model, "messages": messages, "temperature": temperature}
        for k, v in (("tools", tools), ("guardrails", guardrails), ("mcp_servers", mcp_servers),
                     ("response_format", response_format), ("max_tool_iterations", max_tool_iterations),
                     ("max_tokens", max_tokens), ("user", self.user)):
            if v:
                body[k] = v
        raw, hdrs = self._post("/chat/completions", body, headers)
        choices = raw.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content") or ""
        return Reply(content, raw, hdrs, raw.get("otari_tool_calls") or [])

    def fetch(self, url, max_chars=60000):
        body = {"url": url, "max_chars": max_chars}
        if self.user:
            body["user"] = self.user
        return self._post("/fetch", body)

    def search(self, query, **kw):
        body = {"query": query, **kw}
        if self.user:
            body["user"] = self.user
        return self._post("/search", body)
