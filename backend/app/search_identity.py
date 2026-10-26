"""Stable legacy URL identity; source-specific public criteria are adapter-owned."""
import hashlib


def url_identity_v1(url):
    return hashlib.sha256(b'url-v1\0' + url.encode('utf-8')).hexdigest()
