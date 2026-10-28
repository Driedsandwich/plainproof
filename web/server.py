"""A stdlib HTTP/SSE server. Browser runs are serialized, events are reconnectable."""
import asyncio
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from plainproof.common import redact
from plainproof.runner import Config, run
from plainproof.repair import repair

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = {'correct', 'bug_reload', 'bug_counter'}


class Job:
    def __init__(self):
        self.events, self.done = [], False
        self.condition = threading.Condition()

    def emit(self, event):
        with self.condition:
            self.events.append(redact(event))
            if event['type'] in {'done', 'error'}:
                self.done = True
            self.condition.notify_all()


def make_server(port=8765, out='runs', timeout_s=180):
    output = Path(out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    jobs, lock = {}, threading.Lock()
    capacity = threading.Semaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, typ='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', typ)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            host = self.headers.get('Host', '')
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            return host in allowed

        def do_POST(self):
            # Prevent cross-origin requests and DNS rebinding from starting paid runs.
            host = self.headers.get('Host', '')
            if not self.trusted() or self.headers.get('Origin') not in {None, f'http://{host}'}:
                return self.send(403, b'{}')
            if self.path not in {'/api/run', '/api/repair'}:
                return self.send(404, b'{}')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16000 or self.headers.get_content_type() != 'application/json':
                    raise ValueError()
                body = json.loads(self.rfile.read(length))
                is_repair = self.path == '/api/repair'
                if is_repair:
                    with lock:
                        original_job = jobs.get(body['receipt_id'])
                    if original_job is None or not original_job.done or original_job.kind != 'check':
                        raise ValueError()
                    previous = original_job.config
                    receipt = json.loads((Path(previous.out) / 'receipt.json').read_text())
                    if receipt['verdict'] != 'FAIL':
                        raise ValueError()
                else:
                    variant, requirement = body['variant'], body['requirement']
                    if variant not in VARIANTS or not isinstance(requirement, str) or not requirement.strip():
                        raise ValueError()
                    if body.get('model', 'muse-spark-1.3') not in {'muse-spark-1.3', 'muse-wonder-1.0'}:
                        raise ValueError()
                    for key in ('mock', 'approve_confirmations'):
                        if key in body and type(body[key]) is not bool:
                            raise ValueError()
            except (ValueError, KeyError, TypeError, OSError):
                return self.send(400, b'{"error":"Invalid run request"}')
            if not capacity.acquire(blocking=False):
                return self.send(409, b'{"error":"A run is already active"}')
            run_id, job = secrets.token_hex(8), Job()
            job.kind = 'repair' if is_repair else 'check'
            if is_repair:
                # Use the source job's app and offline mode, never new UI selections.
                config = Config(out=str(output / run_id), app_dir=previous.app_dir,
                                mock=previous.mock, approve_confirmations=previous.approve_confirmations,
                                timeout_s=timeout_s)
            else:
                config = Config(out=str(output / run_id), app_dir=str(ROOT / 'demo_app' / variant),
                                requirement=requirement, model=body.get('model', 'muse-spark-1.3'),
                                mock=body.get('mock', True), approve_confirmations=body.get('approve_confirmations', False),
                                timeout_s=timeout_s)
            job.config = config
            with lock:
                jobs[run_id] = job
            def work():
                try:
                    asyncio.run(repair(previous.out, config, on_event=job.emit) if is_repair
                                else run(config, job.emit))
                except Exception as exc:
                    job.emit({'type': 'error', 'message': type(exc).__name__})
                finally:
                    capacity.release()
            threading.Thread(target=work, daemon=True).start()
            self.send(202, json.dumps({'id': run_id}).encode())

        def do_GET(self):
            if not self.trusted():
                return self.send(403, b'{}')
            path = urlsplit(self.path).path
            if path.startswith('/events/'):
                run_id = path.removeprefix('/events/')
                with lock:
                    job = jobs.get(run_id)
                if job is None:
                    return self.send(404, b'{}')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()
                try:
                    cursor = max(0, int(self.headers.get('Last-Event-ID', '-1')) + 1)
                except ValueError:
                    cursor = 0
                try:
                    while True:
                        with job.condition:
                            if cursor == len(job.events) and not job.done:
                                job.condition.wait(timeout=10)
                            events, done = job.events[cursor:], job.done
                        for event in events:
                            self.wfile.write(f'id: {cursor}\ndata: {json.dumps(event)}\n\n'.encode())
                            cursor += 1
                        self.wfile.write(b': heartbeat\n\n')
                        self.wfile.flush()
                        if done:
                            break
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if path == '/':
                return self.send(200, (ROOT / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
            if path.startswith('/runs/'):
                parts = path.split('/')[2:]
                # CLI replay/control receipts live beside web jobs and remain
                # viewable after a restart. Preserve live job screenshot access.
                if len(parts) < 2 or (parts[0] not in jobs and not any(
                        (output / parts[0] / name).is_file() for name in ('receipt.json', 'repair.json'))):
                    return self.send(404, b'{}')
                target = (output / Path(*parts)).resolve()
                if output not in target.parents or not target.is_file():
                    return self.send(404, b'{}')
                return self.send(200, target.read_bytes(), mimetypes.guess_type(target)[0] or 'application/octet-stream')
            return self.send(404, b'{}')
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(port=8765, out='runs', timeout_s=180):
    server = make_server(port, out, timeout_s)
    print(f'Plainproof: http://127.0.0.1:{server.server_port} (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
