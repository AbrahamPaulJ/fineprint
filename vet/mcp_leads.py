"""Our one public service: an MCP server over our lead history, and the prompt-injection guardrail Otari calls.

Hosted Otari has no guardrail service of its own, so a request names ours by URL.

One tool, lead_history(query): what past sweeps and verdicts say about a contest or sponsor. Otari connects from its own
network, so this needs a public https URL when used with otari.ai, such as a tunnel:
    python -I vet/mcp_leads.py --port 8765 --token SECRET
    cloudflared tunnel --url http://localhost:8765
"""
import argparse
import glob
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTOCOL = "2025-06-18"
TOOL = {
    "name": "lead_history",
    "description": "Look up a contest or sponsor in our record of past sweeps and verdicts. Returns matching rows, newest first.",
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Contest or sponsor name."}},
                    "required": ["query"]},
}


def lead_history(query, root=ROOT, limit=8):
    words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 2]
    if not words:
        return {"matches": [], "note": "Query was empty."}
    hits = []
    for path in sorted(glob.glob(os.path.join(root, "reports", "*.json")), reverse=True):
        try:
            r = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        hay = json.dumps({k: r.get(k) for k in ("url", "facts", "reason")}, ensure_ascii=False).lower()
        if any(w in hay for w in words):
            hits.append({"type": "verdict", "url": r.get("url"), "verdict": r.get("verdict"), "reason": r.get("reason")})
    for path in sorted(glob.glob(os.path.join(root, "leads", "sweep-*.json")), reverse=True)[:3]:
        try:
            rows = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for r in rows:
            if all(w in (r.get("title") or "").lower() for w in words[:2]):
                hits.append({"type": "lead", "seen_in": os.path.basename(path), "title": r.get("title"), "url": r.get("url"),
                             "cash_usd": r.get("cash_usd"), "deadline": r.get("deadline"), "entrants": r.get("entrants")})
    return {"matches": hits[:limit], "total": len(hits)}


# Phrases that talk to an AI reader rather than a human entrant. Rules pages mention "AI" constantly, so these stay specific.
INJECTION = re.compile(
    r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+|your\s+)*(previous|prior|above|earlier|system)\s+(instructions?|prompts?|rules)"
    r"|(if|when)\s+you\s+are\s+an?\s+(ai|llm|language model|agent|assistant)\b"
    r"|\b(ai|llm)\s+(agents?|assistants?|models?|readers?)\s*(reading|must|should|are instructed)\b"
    r"|\b(reveal|print|repeat)\s+your\s+(system\s+)?(prompt|instructions)"
    r"|do\s+not\s+(tell|inform|mention\s+to)\s+the\s+(user|human)"
    r"|</?(system|assistant)>|\[/?INST\]|<\|im_start\|>", re.I)


def validate(body):
    """Otari's guardrails-service contract: POST /validate -> valid false means flagged."""
    hits = [m.group(0)[:80] for m in INJECTION.finditer(body.get("input_text") or "")]
    return {"profile": body.get("profile"), "result": {"valid": not hits, "score": 1.0 if hits else 0.0,
                                                       "explanation": "; ".join(hits[:3]) or None}}


def handle_rpc(msg, root=ROOT):
    """One JSON-RPC message in, one response out (None for a notification)."""
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        return None
    if method == "initialize":
        result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "fineprint-leads", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "ping":
        result = {}
    elif method == "tools/call":
        params = msg.get("params") or {}
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "Unknown tool"}}
        out = lead_history((params.get("arguments") or {}).get("query"), root)
        result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}], "isError": False}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def make_handler(token=None, root=ROOT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print("mcp:", fmt % args)

        def _send(self, code, body=None):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # no server-initiated MCP stream
            if self.path.rstrip("/").endswith("/profiles"):
                return self._send(200, [{"name": "prompt-injection", "guardrail_name": "fineprint_rules"}])
            self._send(405)

        def do_POST(self):
            if self.path.rstrip("/").endswith("/validate"):  # holds nothing private, and Otari sends no token on a request's url
                return self._send(200, validate(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))))
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                return self._send(401, {"error": "unauthorized"})
            try:
                msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except ValueError:
                return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            if isinstance(msg, list):
                out = [r for r in (handle_rpc(m, root) for m in msg) if r]
                return self._send(200, out) if out else self._send(202)
            out = handle_rpc(msg, root)
            return self._send(200, out) if out else self._send(202)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=os.environ.get("FINEPRINT_MCP_TOKEN"))
    a = ap.parse_args()
    print(f"fineprint-leads MCP on http://localhost:{a.port} (token {'on' if a.token else 'off'})")
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.token)).serve_forever()


if __name__ == "__main__":
    main()
