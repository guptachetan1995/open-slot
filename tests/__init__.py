import socket

_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_connect = socket.socket.connect
_lookup = socket.getaddrinfo


def _guarded_connect(self, address):
    if not (isinstance(address, tuple) and address[0] in _LOOPBACK):
        raise OSError("the tests are offline: only loopback connections are allowed")
    return _connect(self, address)


def _guarded_lookup(host, *args, **kwargs):
    if host not in _LOOPBACK and host is not None:
        raise OSError("the tests are offline: only loopback names are resolved")
    return _lookup(host, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.getaddrinfo = _guarded_lookup
