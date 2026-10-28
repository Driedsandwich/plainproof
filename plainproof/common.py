"""Small shared protocol, accounting, and local app server helpers."""
import base64
import functools
import os
import re
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PRICES = {'muse-spark-1.3': (1.25, .15, 4.25), 'muse-wonder-1.0': (4., .20, 20.)}
CONFIRM = 'computer_action_requires_confirmation'
VIEWPORT = {'width': 1024, 'height': 768}


def image_url(png):
    # Transport only: never persist this string in a receipt or event.
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def output_text(response):
    return '\n'.join(c['text'] for item in response.get('output', [])
                     if item.get('type') == 'message' for c in item.get('content', [])
                     if c.get('type') == 'output_text')


def as_dict(response):
    return response if isinstance(response, dict) else response.model_dump()


def empty_usage():
    return dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)


def account(receipt, role, response):
    receipt['model_calls'][role] += 1
    usage = response.get('usage') or {}
    target = receipt['usage'][role]
    target['input_tokens'] += usage.get('input_tokens', 0) or 0
    target['output_tokens'] += usage.get('output_tokens', 0) or 0
    target['cached_input_tokens'] += (usage.get('input_tokens_details') or {}).get('cached_tokens', 0) or 0


def cost(model, usage):
    a, b, c = PRICES[model]
    return round(((usage['input_tokens'] - usage['cached_input_tokens']) * a +
                  usage['cached_input_tokens'] * b + usage['output_tokens'] * c) / 1e6, 8)


def redact(value):
    """Defense in depth for user/model strings; never serialize the credential."""
    if isinstance(value, str):
        for name in ('MODEL_API_KEY', 'META_API_KEY'):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return re.sub(r'data:image/[^\s"\']+;base64,[A-Za-z0-9+/=]+', '[IMAGE REMOVED]', value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {redact(k): redact(v) for k, v in value.items()}
    return value


def origin(url):
    u = urlsplit(url)
    if u.scheme not in {'http', 'https'} or not u.hostname or u.username or u.password:
        raise ValueError('App URL must be HTTP(S) without credentials')
    return (u.scheme, u.hostname.lower(), u.port or (443 if u.scheme == 'https' else 80))


def is_local(url):
    return urlsplit(url).hostname in {'127.0.0.1', 'localhost', '::1'}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@contextmanager
def app_server(directory=None, url=None):
    if url:
        origin(url)
        parts = urlsplit(url)
        yield urlunsplit(parts._replace(path=parts.path or '/'))
        return
    path = Path(directory).resolve()
    if not path.is_dir():
        raise ValueError('App directory does not exist')
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
