from __future__ import annotations

import io

from gateway.control.error_middleware import error_guard


class _FakeHandler:
    def __init__(self):
        self.status_codes: list[int] = []
        self.headers: list[tuple[str, str]] = []
        self.ended = False
        self.wfile = io.BytesIO()

    def send_response(self, code: int):
        self.status_codes.append(code)

    def send_header(self, key: str, value: str):
        self.headers.append((key, value))

    def end_headers(self):
        self.ended = True


def test_error_guard_ignores_client_disconnect():
    handler = _FakeHandler()

    @error_guard
    def _handler_method(self):
        raise BrokenPipeError("client disconnected")

    result = _handler_method(handler)

    assert result is None
    assert handler.status_codes == []
    assert handler.wfile.getvalue() == b""
