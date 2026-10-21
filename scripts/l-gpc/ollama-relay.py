"""Loopback-only bridge from WSL SSH forwarding to native Windows Ollama."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json,subprocess
class Handler(BaseHTTPRequestHandler):
    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def forward(self):
        allowed={'/api/tags','/api/ps','/api/version','/api/chat','/api/generate','/api/show'}
        if self.path not in allowed:
            self.send_error(404);return
        length=int(self.headers.get('Content-Length',0))
        if length>32*1024*1024:
            self.send_error(413);return
        body=self.rfile.read(length).decode() if length else ''
        if body and json.loads(body).get('stream',False):
            self.send_error(400,'Use stream=false');return
        try:
            result=subprocess.run(['/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-File','D:\\PacificGym\\tools\\ollama-forward.ps1'],input=json.dumps({'path':self.path,'method':self.command,'body':body}),text=True,capture_output=True,timeout=310)
            data=result.stdout.encode() if result.returncode==0 else json.dumps({'error':'Windows Ollama request failed; inspect target runtime'}).encode()
            self.send_response(200 if result.returncode==0 else 502)
        except subprocess.TimeoutExpired:
            data=b'{"error":"Windows Ollama timeout"}';self.send_response(504)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
ThreadingHTTPServer(('127.0.0.1',11436),Handler).serve_forever()
