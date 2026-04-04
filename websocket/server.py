#!/usr/bin/env python3
"""
Dev server for websocket/index.html
Run:   python server.py
Open:  http://localhost:5501
"""
import http.server
import socketserver
import os

PORT = 5501
DIRECTORY = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} - {fmt % args}")


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    os.chdir(DIRECTORY)
    with ReusableTCPServer(("", PORT), Handler) as httpd:
        print(f"🌐 Serving websocket/index.html at http://localhost:{PORT}")
        print(f"   Directory : {DIRECTORY}")
        print(f"   Press Ctrl+C to stop.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n⛔ Server stopped.")
