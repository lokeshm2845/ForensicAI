"""Stand-in for ngrok's FREE plan: browser-like requests WITHOUT the 'ngrok-skip-browser-warning' header get an HTML warning page
that carries no CORS headers (=> the browser reports a CORS error). Requests with the header are proxied to the API."""
import sys, threading, httpx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UP = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8001
STATS = {"interstitial": 0, "proxied": 0}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _go(self):
        n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n) if n else None
        if self.command != "OPTIONS" and "ngrok-skip-browser-warning" not in self.headers:
            STATS["interstitial"] += 1
            page = b"<html><body>You are about to visit this site (ngrok warning page)</body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(page))); self.end_headers(); self.wfile.write(page); return
        STATS["proxied"] += 1
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "content-length", "accept-encoding")}
        with httpx.stream(self.command, UP + self.path, headers=hdrs, content=body, timeout=None) as r:
            self.send_response(r.status_code)
            for k, v in r.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"): self.send_header(k, v)
            streaming = "text/event-stream" in r.headers.get("content-type", "")
            if streaming: self.send_header("Transfer-Encoding", "chunked")
            else: data = r.read(); self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if streaming:
                try:
                    for chunk in r.iter_raw():
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"); self.wfile.flush()
                except Exception: pass
            else: self.wfile.write(data)
    do_GET = do_POST = do_OPTIONS = _go

if __name__ == "__main__":
    s = ThreadingHTTPServer(("127.0.0.1", PORT), H); print("fake ngrok on", PORT, "->", UP, flush=True); s.serve_forever()
