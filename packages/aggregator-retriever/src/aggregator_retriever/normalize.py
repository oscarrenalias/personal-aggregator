from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

_TRACKING_PARAMS = frozenset(
    {"fbclid", "gclid", "mc_eid", "igshid"}
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port

    if port and _DEFAULT_PORTS.get(scheme) == port:
        port = None

    netloc = host if port is None else f"{host}:{port}"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k != "" and not k.startswith("utm_") and k not in _TRACKING_PARAMS
    ]
    query_pairs.sort(key=lambda kv: kv[0])
    query = urlencode(query_pairs)

    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, parsed.params, query, ""))
