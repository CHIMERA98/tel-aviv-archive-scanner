import hashlib
import hmac

from scanner.edge_client import sign


def test_signature_format_and_value():
    key, body = b"secret", b'{"stage":"maps"}'
    header = sign(key, "post", "/internal/scans/abc/progress", body, now=1_700_000_000)
    t, v1 = (part.split("=", 1)[1] for part in header.split(","))
    assert t == "1700000000"
    message = f"1700000000.POST./internal/scans/abc/progress.{hashlib.sha256(body).hexdigest()}"
    assert v1 == hmac.new(key, message.encode(), hashlib.sha256).hexdigest()


def test_signature_changes_with_body():
    a = sign(b"k", "PUT", "/p", b"one", now=1)
    b = sign(b"k", "PUT", "/p", b"two", now=1)
    assert a != b
