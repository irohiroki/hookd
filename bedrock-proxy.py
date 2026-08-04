#!/usr/bin/env python3
"""
bedrock-proxy — HTTPS MITM proxy that injects AWS_BEARER_TOKEN_BEDROCK into
requests from the claude binary to the Bedrock OpenAI-compatible endpoint.

claude runs with a dummy token; this proxy intercepts via HTTPS_PROXY and
substitutes the real token before forwarding to Bedrock.
"""
import http.client
import http.server
import logging
import os
import signal
import socket
import socketserver
import ssl
import sys
import threading

PROXY_PORT = int(os.environ.get("BEDROCK_PROXY_PORT", "8888"))
TOKEN = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
SERVER_CERT = os.environ.get("BEDROCK_PROXY_CERT", "/etc/bedrock-proxy/server.crt")
SERVER_KEY = os.environ.get("BEDROCK_PROXY_KEY", "/etc/bedrock-proxy/server.key")

HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "proxy-connection",
])

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_ssl_ctx = None
_ssl_ctx_lock = threading.Lock()


def get_ssl_context():
    global _ssl_ctx
    with _ssl_ctx_lock:
        if _ssl_ctx is None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=SERVER_CERT, keyfile=SERVER_KEY)
            _ssl_ctx = ctx
    return _ssl_ctx


def parse_headers(rfile):
    headers = {}
    while True:
        line = rfile.readline(8192).decode(errors="replace").rstrip("\r\n")
        if not line:
            break
        key, _, value = line.partition(": ")
        if key:
            headers[key] = value.strip()
    return headers


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
            self.send_error(405)

    def do_CONNECT(self):
        self.close_connection = True
        host, _, port_str = self.path.partition(":")
        port = int(port_str) if port_str else 443

        self.send_response(200, "Connection Established")
        self.end_headers()

        ctx = get_ssl_context()
        try:
            tls_sock = ctx.wrap_socket(self.connection, server_side=True)
        except ssl.SSLError as exc:
            logger.warning("TLS handshake failed for %s: %s", host, exc)
            return

        try:
            self._intercept(tls_sock, host, port)
        finally:
            try:
                tls_sock.close()
            except OSError:
                pass

    def _intercept(self, client_sock, host, port):
        rfile = client_sock.makefile("rb")
        try:
            raw_line = rfile.readline(65537)
            if not raw_line:
                return
            parts = raw_line.decode().strip().split(None, 2)
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1]
            req_headers = parse_headers(rfile)
            content_length = int(req_headers.get("Content-Length", 0))
            body = rfile.read(content_length) if content_length else None
        finally:
            rfile.close()

        req_headers["Authorization"] = f"Bearer {TOKEN}"
        upstream_headers = {
            k: v for k, v in req_headers.items()
            if k.lower() not in HOP_BY_HOP
        }

        host_header = req_headers.get("Host", host)
        upstream_host = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
        logger.info("MITM %s %s%s", method, upstream_host, path)

        conn = http.client.HTTPSConnection(upstream_host, port)
        try:
            conn.request(method, path, body=body, headers=upstream_headers)
            resp = conn.getresponse()
            body_data = resp.read()

            client_sock.sendall(
                f"HTTP/1.1 {resp.status} {resp.reason}\r\n".encode()
            )
            for h, v in resp.getheaders():
                if h.lower() not in HOP_BY_HOP:
                    client_sock.sendall(f"{h}: {v}\r\n".encode())
            client_sock.sendall(
                f"Content-Length: {len(body_data)}\r\n\r\n".encode()
            )
            client_sock.sendall(body_data)
            logger.info("← %d (%d bytes)", resp.status, len(body_data))
        except Exception as exc:
            logger.error("upstream error: %s", exc)
            try:
                client_sock.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except OSError:
                pass
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


class LocalProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, exc_val, _ = sys.exc_info()
        if isinstance(exc_val, (BrokenPipeError, ConnectionResetError, ValueError)):
            return
        super().handle_error(request, client_address)


def main():
    if not TOKEN:
        logger.error("AWS_BEARER_TOKEN_BEDROCK is not set")
        sys.exit(1)

    server = LocalProxyServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
    logger.info("MITM proxy on 127.0.0.1:%d", PROXY_PORT)

    def shutdown(sig, frame):
        logger.info("shutting down")
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
