"""Tiny target service: serves the instance token at /token (doc 02 §2)."""

import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/token":
            body = f"service token: {os.environ.get('FLAG', '')}\n".encode()
            self.send_response(200)
        else:
            body = b"ok\n"
            self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence default stderr logging
        return


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
