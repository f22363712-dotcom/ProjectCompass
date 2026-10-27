"""Local protocol capabilities. This does not isolate hostile same-OS code."""
import hmac
from http.cookies import SimpleCookie, CookieError
import secrets


class RequestTooLarge(ValueError):
    pass


class OwnerBrowserAccess:
    def __init__(self):
        self._cookie = secrets.token_urlsafe(32)

    def local_origins(self, server):
        port = server.server_address[1]
        return {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}

    def bootstrap_allowed(self, handler):
        headers = handler.headers
        origins = self.local_origins(handler.server)
        return (headers.get('Host', '').lower() in {origin[7:] for origin in origins} and
                not headers.get('Authorization') and
                headers.get('Sec-Fetch-Site', '').lower() in {'', 'none', 'same-origin', 'same-site'} and
                (not headers.get('Origin') or headers.get('Origin') in origins))

    def cookie_header(self):
        return f'compass_owner={self._cookie}; Path=/; HttpOnly; SameSite=Strict'

    def authorized(self, handler):
        if not self.bootstrap_allowed(handler) or handler.headers.get('Origin') not in self.local_origins(handler.server):
            return False
        try:
            cookies = SimpleCookie();cookies.load(handler.headers.get('Cookie', ''))
            value = cookies.get('compass_owner')
            return value is not None and value.value.isascii() and hmac.compare_digest(value.value, self._cookie)
        except CookieError:
            return False


def route_agent_request(handler, path, method):
    if not path.startswith('/api/agent/'):
        return False
    inbox = handler.server.agent_inbox
    if inbox is None:
        handler._reject(404, {'error': 'Agent API is not enabled'})
        return True
    try:
        client = inbox.authenticate(handler.headers.get('Authorization'))
        if method == 'POST' and handler.headers.get('Origin') is not None:
            handler._reject(403, {'error': 'Agent submissions require a non-browser client'})
            return True
        if method == 'GET' and path == '/api/agent/state':
            handler._json(200, handler.server.dashboard_state())
        elif method == 'POST' and path in {'/api/agent/events', '/api/agent/proposals'}:
            payload = handler._request_json()
            with handler.server.state_lock:
                result = inbox.append_events(client, payload) if path.endswith('/events') else inbox.propose(client, payload)
            handler._json(200, result)
        else:
            handler._reject(404, {'error': 'unknown Agent protocol route'})
    except PermissionError:
        handler._reject(401, {'error': 'Agent credential required'})
    except RequestTooLarge:
        handler._reject(413, {'error': 'Agent request exceeds256KiB'})
    except (ValueError, OSError):
        # Never reflect submitted text, credentials or filesystem exceptions.
        handler._json(400, {'error': 'Agent request rejected; verify protocol fields and immutable source references'})
    return True
