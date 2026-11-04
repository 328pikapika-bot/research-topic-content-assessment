#!/usr/bin/env python
"""Small local reverse proxy for NSTL retrieval during Docker Desktop testing."""

from __future__ import annotations

import argparse
import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


# 允许转发的路径白名单（防止 SSRF 攻击）
_ALLOWED_PATHS: set[str] = {
    "/get_milvus_by_llama_index",
}


class RetrievalProxy(BaseHTTPRequestHandler):
    target_base: str = "http://127.0.0.1:37899"
    timeout: int = 180

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def do_OPTIONS(self) -> None:
        self._forward()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    def _is_path_allowed(self, path: str) -> bool:
        """校验转发路径是否在白名单中，防止 SSRF 攻击。"""
        return path in _ALLOWED_PATHS

    def _forward(self) -> None:
        target = urlsplit(self.target_base)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
        path = self.path
        if not path.startswith("/"):
            path = "/" + path

        # 路径白名单校验
        if not self._is_path_allowed(path):
            message = f"Forbidden: path '{path}' not in allowed set".encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return

        conn_cls = http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(target.hostname, target.port, timeout=self.timeout)
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }
        if body:
            headers["Content-Length"] = str(len(body))
        try:
            conn.request(self.command, path, body=body if body else None, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in {"transfer-encoding", "connection"}:
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # pragma: no cover - diagnostic server
            message = f"NSTL retrieval proxy failed: {type(exc).__name__}: {exc}".encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
        finally:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Proxy local Docker containers to NSTL retrieval.")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", default=37898, type=int)
    parser.add_argument("--target-base", default="http://127.0.0.1:37899")
    parser.add_argument("--timeout", default=180, type=int)
    args = parser.parse_args()

    RetrievalProxy.target_base = args.target_base.rstrip("/")
    RetrievalProxy.timeout = args.timeout
    server = ThreadingHTTPServer((args.listen_host, args.listen_port), RetrievalProxy)
    print(
        f"NSTL retrieval proxy listening on http://{args.listen_host}:{args.listen_port} "
        f"-> {RetrievalProxy.target_base}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
