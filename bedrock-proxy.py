#!/usr/bin/env python3
"""
bedrock-proxy — Unix socket HTTP proxy for the AWS Bedrock OpenAI-compatible
endpoint. Holds AWS_BEARER_TOKEN_BEDROCK and forwards requests from local
clients without exposing the token to user scripts.
"""
import http.client
import http.server
import logging
import os
import signal
import socket
import socketserver
import sys
import urllib.parse

TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
BEDROCK_BASE_URL = os.environ.get(
    "BEDROCK_BASE_URL", "https://bedrock.us-east-1.amazonaws.com/v1"
)
SOCKET_PATH = os.environ.get(
    "BEDROCK_PROXY_SOCK", "/run/bedrock-proxy/proxy.sock"
)

_parsed = urllib.parse.urlparse(BEDROCK_BASE_URL)
UPSTREAM_HOST = _parsed.netloc
UPSTREAM_PATH_PREFIX = _parsed.path.rstrip("/")

HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


class UnixHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self):
        if os.path.exists(self.server_address):
            os.unlink(self.server_address)
        super().server_bind()
        os.chmod(self.server_address, 0o666)

    def get_request(self):
        request, _ = self.socket.accept()
        return request, ("unix", 0)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/up":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._forward("GET", b"")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        self._forward("POST", body)

    def _forward(self, method, body):
        upstream_path = UPSTREAM_PATH_PREFIX + self.path
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "Accept": self.headers.get("Accept", "*/*"),
        }
        if body:
            headers["Content-Length"] = str(len(body))

        try:
            conn = http.client.HTTPSConnection(UPSTREAM_HOST)
            conn.request(method, upstream_path, body=body or None, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for h, v in resp.getheaders():
                if h.lower() not in HOP_BY_HOP:
                    self.send_header(h, v)
            self.end_headers()
            while chunk := resp.read(8192):
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception as exc:
            logger.error("upstream error: %s", exc)
            try:
                self.send_error(502, str(exc))
            except Exception:
                pass

    def log_message(self, fmt, *args):
        logger.info("unix %s", fmt % args)

    def address_string(self):
        return "unix"


def main():
    if not TOKEN:
        logger.error("AWS_BEARER_TOKEN_BEDROCK is not set")
        sys.exit(1)

    server = UnixHTTPServer(SOCKET_PATH, ProxyHandler)
    logger.info("listening on %s → %s", SOCKET_PATH, BEDROCK_BASE_URL)

    def shutdown(sig, frame):
        logger.info("shutting down")
        server.server_close()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
