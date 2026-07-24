"""Validate facts in local-model responses that can be checked exactly."""

import re
from urllib.parse import urlsplit, urlunsplit


def has_unsupported_urls(response, source):
    """Return whether a response contains a URL absent from its source facts."""
    response_urls = _extract_urls(response)
    if not response_urls:
        return False

    supported_urls = {
        _canonical_url(url)
        for url in _extract_urls(source)
    }
    return any(
        _canonical_url(url) not in supported_urls
        for url in response_urls
    )


def _extract_urls(value):
    text = str(value or '')
    candidates = re.findall(
        r'https?://[^\s<>\[\]()"^\']+',
        text,
        flags=re.IGNORECASE,
    )
    candidates.extend(
        re.findall(
            r'(?<![@\w])www\.[^\s<>\[\]()"^\']+',
            text,
            flags=re.IGNORECASE,
        )
    )
    candidates.extend(
        re.findall(
            (
                r'(?<![@\w])(?:[a-z0-9-]+\.)+[a-z]{2,}'
                r'(?:/[^\s<>\[\]()"^\']*)?'
            ),
            text,
            flags=re.IGNORECASE,
        )
    )
    candidates.extend(
        re.findall(
            (
                r'(?<![\w/])/(?!/)'
                r'(?:[a-z0-9._~-]+/)*[a-z0-9._~-]+/?'
            ),
            text,
            flags=re.IGNORECASE,
        )
    )
    candidates.extend(
        target
        for target in re.findall(r'\]\(\s*([^)]+?)\s*\)', text)
        if target.startswith(('/', 'http://', 'https://', 'www.'))
    )
    return tuple(dict.fromkeys(
        candidate.strip('\'"<>[](){}').rstrip('.,;:!?')
        for candidate in candidates
        if candidate
    ))


def _canonical_url(value):
    value = value.strip()
    if value.startswith('/'):
        return f'path:{value.rstrip("/") or "/"}'
    if '://' not in value:
        value = f'https://{value}'

    parsed = urlsplit(value)
    path = parsed.path.rstrip('/') or '/'
    return urlunsplit((
        parsed.scheme.casefold(),
        parsed.netloc.casefold(),
        path,
        parsed.query,
        parsed.fragment,
    ))
