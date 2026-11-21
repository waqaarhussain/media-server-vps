#!/usr/bin/env python3
"""Local-only dashboard and honest service/machine status behind Nginx authentication."""
import http.server
import json
from pathlib import Path
import shutil
import socket
import sys
import psutil

ROOT=Path(__file__).resolve().parent
POOL=sys.argv[1] if len(sys.argv)>1 else '/srv/media'


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3):return True
    except OSError:return False


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        if self.path=='/api/status':
            disk=shutil.disk_usage(POOL);ram=psutil.virtual_memory()
            data={'cpu':psutil.cpu_percent(),'memory':ram.percent,'disk':{
                  'total':disk.total,'used':disk.used,'free':disk.free},'pool':POOL,
                  'uptime':float(Path('/proc/uptime').read_text().split()[0]),
                  'services':{name:listening(port) for name,port in {
                    'radarr':17878,'prowlarr':19696,'qbittorrent':18080,'files':18082,'ftp':21}.items()}}
            content=json.dumps(data).encode();kind='application/json'
        elif self.path in ('/','/index.html','/app.js','/style.css'):
            name='index.html' if self.path=='/' else self.path[1:]
            content=(ROOT/name).read_bytes()
            kind={'html':'text/html; charset=utf-8','js':'application/javascript','css':'text/css'}[name.rsplit('.',1)[1]]
        else:
            self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',kind)
        self.send_header('Cache-Control','no-store' if self.path=='/api/status' else 'no-cache')
        self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)


if __name__=='__main__':
    http.server.ThreadingHTTPServer(('127.0.0.1',18000),Handler).serve_forever()
