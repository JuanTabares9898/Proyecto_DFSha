"""Servidores separados TLS y mTLS. No se confía en cabeceras de proxy."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

from .common import Error, packed


def serve(app, config):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, fmt, *args):
            logging.info('%s %s', config['node_id'], fmt % args)

        def action(self):
            try:
                self.connection.settimeout(20)
                parsed = urlsplit(self.path)
                path = parsed.path
                if path.startswith('/internal/') != self.server.internal:
                    raise Error(404, 'ruta no expuesta en este puerto')
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > config.get('block_size', 8388608)+1048576:
                    raise Error(413, 'cuerpo demasiado grande')
                body = self.rfile.read(length) if length else b''
                if len(body) != length:
                    raise Error(400, 'transferencia incompleta')
                peer = self.connection.getpeercert()
                cn = next((value for attrs in peer.get('subject',[]) for key,value in attrs
                           if key=='commonName'), None) if peer else None
                answer = app.dispatch(self.command, path, parse_qs(parsed.query), body,
                                      dict(self.headers), self.server.internal, cn)
                status, data = answer if isinstance(answer, tuple) else (200, answer)
                content_type = 'application/octet-stream' if isinstance(data, bytes) else 'application/json'
                encoded = data if isinstance(data, bytes) else packed(data)
            except Error as exc:
                status, encoded, content_type = exc.status, packed({'error': exc.message}), 'application/json'
                logging.warning('request_denied node=%s method=%s status=%s path=%s',config['node_id'],self.command,status,self.path.split('?')[0])
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                status, encoded, content_type = 400, packed({'error':'solicitud inválida'}), 'application/json'
            except Exception:
                logging.exception('error de servidor')
                status, encoded, content_type = 500, packed({'error':'error interno'}), 'application/json'
            try:
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(encoded)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

        do_GET = do_POST = do_PUT = do_DELETE = action

    servers=[]
    for internal in [False, True]:
        server = ThreadingHTTPServer(('0.0.0.0', config['internal_port' if internal else 'port']), Handler)
        server.daemon_threads = True
        server.internal = internal
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(config['service_cert'], config['service_key'])
        if internal:
            context.load_verify_locations(cafile=config['node_ca'])
            context.verify_mode = ssl.CERT_REQUIRED
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        servers.append(server)
    try:
        threading.Event().wait()
    finally:
        for server in servers: server.shutdown()
