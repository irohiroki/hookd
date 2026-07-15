"""HTTP request handler."""

import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler

from runner import (MAX_BODY_BYTES, build_webhook_env, run_script_async,
                    run_script_sync)
from user import _make_preexec


def verify_signature(secret, body_bytes, signature_header):
    """Return True if HMAC-SHA256 of body matches the X-Hub-Signature-256 header."""
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def find_route(routes, path, payload):
    """Return the first route whose path and all match conditions are satisfied."""
    for route in routes:
        if route['path'] != path:
            continue
        if all(payload.get(k) == v for k, v in route.get('match', {}).items()):
            return route
    return None


class HookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        logger = self.server.logger
        path = self.path.split('?')[0]

        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            self._send_json(400, {'error': 'invalid Content-Length'})
            return
        if not (0 <= content_length <= MAX_BODY_BYTES):
            self._send_json(413, {'error': 'payload too large'})
            return
        body_bytes = self.rfile.read(content_length) if content_length else b''

        logger.info('POST %s from %s', path, self.client_address[0])

        try:
            payload = json.loads(body_bytes) if body_bytes else {}
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError:
            self._send_json(400, {'error': 'invalid JSON body'})
            return

        with self.server.routes_lock:
            route = find_route(self.server.routes, path, payload)

        if route is None:
            logger.warning('no route matched: %s', path)
            self._send_json(404, {'error': 'no matching route'})
            return

        secret = route.get('secret')
        if secret:
            sig = self.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(secret, body_bytes, sig):
                logger.warning('signature verification failed: %s', path)
                self._send_json(401, {'error': 'invalid signature'})
                return

        owner = route.get('_owner')
        env = build_webhook_env(route, payload, body_bytes, path)
        preexec_fn = _make_preexec(owner)
        script = route['script']

        if route.get('async'):
            run_script_async(script, env, logger, preexec_fn=preexec_fn)
            self._send_json(202, {'status': 'accepted'})
        else:
            rc, output = run_script_sync(
                script, env, route.get('timeout', 30), logger, preexec_fn=preexec_fn,
            )
            if rc == 0:
                self._send_json(200, {'status': 'ok', 'exit_code': rc})
            else:
                self._send_json(500, {'status': 'error', 'exit_code': rc, 'output': output})
