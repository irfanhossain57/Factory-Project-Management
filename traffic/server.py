"""Small, dependency-free REST adapter for a local assessment demonstration."""
import argparse
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from .domain import DomainError
from .repository import Repository
from .service import Service
from .controller import SimulatedController

LOGGER = logging.getLogger(__name__)


def handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'FactoryTraffic/1.0'

        def log_message(self, fmt, *args):
            LOGGER.info('%s %s', self.client_address[0], fmt % args)

        def send(self, data, status=200, content_type='application/json; charset=utf-8'):
            body = json.dumps(data).encode() if content_type.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.handle_api('GET')

        def do_POST(self):
            self.handle_api('POST')

        def handle_api(self, method):
            try:
                url = urlsplit(self.path)
                parts = [unquote(p) for p in url.path.strip('/').split('/')]
                data = None
                if method == 'POST':
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise DomainError('Use Content-Type: application/json', 415)
                    try:
                        length = int(self.headers.get('Content-Length', '0'))
                    except ValueError:
                        raise DomainError('Invalid Content-Length', 400)
                    if not 0 < length <= 65536:
                        raise DomainError('JSON body must be 1..65536 bytes', 413)
                    self.connection.settimeout(5)
                    try:
                        data = json.loads(self.rfile.read(length), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
                    except (ValueError, UnicodeDecodeError):
                        raise DomainError('Malformed JSON', 400)
                    if not isinstance(data, dict):
                        raise DomainError('JSON body must be an object', 422)
                if method == 'GET' and url.path in ('/', '/app.js', '/style.css'):
                    name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}[url.path]
                    return self.send(Path(__file__).with_name('static').joinpath(name).read_bytes(), content_type=mime + '; charset=utf-8')
                if method == 'GET' and url.path == '/health':
                    return self.send({'status': 'ok'})
                if parts == ['api', 'junctions']:
                    if method == 'GET':
                        return self.send(service.list())
                    return self.send(service.create(data), 201)
                if method == 'POST' and parts in (['api', 'sensor-events'], ['api', 'device-events']):
                    response, code = service.event(data, parts[1] == 'device-events')
                    return self.send(response, code)
                if method == 'POST' and parts == ['api', 'controller-events']:
                    return self.send(service.ack(data))
                if len(parts) >= 3 and parts[:2] == ['api', 'junctions']:
                    jid = parts[2]
                    if method == 'GET' and (len(parts) == 3 or parts[3:] == ['status']):
                        return self.send(service.status(jid))
                    if method == 'GET' and parts[3:] == ['history']:
                        q = parse_qs(url.query)
                        try:
                            limit = int(q.get('limit', ['100'])[0])
                            before = int(q['before'][0]) if 'before' in q else None
                        except ValueError:
                            raise DomainError('limit and before must be integers', 422)
                        if not 1 <= limit <= 200 or before is not None and before < 1:
                            raise DomainError('limit must be 1..200; before must be positive', 422)
                        return self.send(service.history(jid, limit, before))
                    if method == 'POST' and parts[3:] == ['commands']:
                        return self.send(service.command(jid, data))
                raise DomainError('Route not found', 404)
            except DomainError as exc:
                self.send({'error': str(exc)}, exc.status)
            except (TimeoutError, ConnectionError, BrokenPipeError):
                LOGGER.warning('Client connection ended or timed out')
            except Exception:
                LOGGER.exception('Unhandled request error')
                self.send({'error': 'Internal server error; see server log'}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description='Factory traffic assessment server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--db', default='data/traffic.sqlite3')
    parser.add_argument('--simulate', action='store_true', help='Automatically acknowledge simulated controller commands')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # Advisory lock prevents a second control loop from driving the same database.
    # SQLite serializes requests; this separate lifetime lock protects startup recovery.
    import os
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    lock = open(args.db + '.lock', 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        parser.error('Another server owns this database')
    service = Service(Repository(args.db))
    service.boot()
    stop = threading.Event()
    adapter = SimulatedController() if args.simulate else None

    def loop():
        while not stop.wait(0.2):
            try:
                service.tick()
                if adapter:
                    adapter.poll(service)
            except Exception:
                LOGGER.exception('Control loop failed; next cycle will retry')
    server = ThreadingHTTPServer((args.host, args.port), handler_for(service))
    worker = threading.Thread(target=loop, daemon=True)
    worker.start()
    LOGGER.info('Dashboard: http://%s:%s | auto-ACK simulator: %s', args.host, args.port, bool(adapter))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=3)
        lock.close()
