"""Middlewares for fortissimusbellator project."""
import secrets


SELF_SRC = ["'self'"]
NONE_SRC = ["'none'"]


class ContentSecurityPolicyMiddleware:
    """Middleware to set Content Security Policy headers."""

    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.

        request.csp_nonce = secrets.token_urlsafe(16)
        response = self.get_response(request)

        default_src = SELF_SRC

        # Script sources: must use nonce,
        # except for admin which needs unsafe-inline
        if request.path.startswith("/admin/"):
            script_src = SELF_SRC + ["'unsafe-inline'"]
        else:
            script_src = SELF_SRC + [f"'nonce-{request.csp_nonce}'"]

        # local scripts, inline for small injected snippets,
        # and common CDNs / embeds (tawk, jsdelivr)
        script_src += [
            'https://cdn.jsdelivr.net',
            '*.google.com',
            '*.gstatic.com',
            '*.tawk.to',
        ]

        # Styles: allow Google Fonts and inline styles used by some components
        style_src = SELF_SRC + [
            "'unsafe-inline'",
            'https://fonts.googleapis.com',
            'https://embed.tawk.to',
        ]

        # Fonts: Google Fonts host
        font_src = SELF_SRC + [
            'https://fonts.gstatic.com',
            'https://embed.tawk.to',
        ]

        # Images: self and data URIs; allow social thumbnails and CDN images
        img_src = SELF_SRC + [
            'data:',
            'https://www.facebook.com',
            'https://www.instagram.com',
            'https://cdn.jsdelivr.net',
            'https://embed.tawk.to',
            '*.openstreetmap.org',
        ]

        # Connections (XHR, WebSocket): allow same-origin and tawk endpoints
        connect_src = SELF_SRC + [
            '*.tawk.to',
            'wss://*.tawk.to',
        ]

        # Frames: none by default (prevents embedding)
        frame_src = SELF_SRC + [
            '*.google.com',
            '*.tawk.to',
        ]

        # Frames: none by default (prevents embedding)
        frame_ancestors_src = NONE_SRC

        # Other directives
        media_src = SELF_SRC
        object_src = NONE_SRC

        policy_parts = [
            f"default-src {' '.join(default_src)}",
            f"script-src {' '.join(script_src)}",
            f"style-src {' '.join(style_src)}",
            f"font-src {' '.join(font_src)}",
            f"img-src {' '.join(img_src)}",
            f"connect-src {' '.join(connect_src)}",
            f"frame-src {' '.join(frame_src)}",
            f"frame-ancestors {' '.join(frame_ancestors_src)}",
            f"media-src {' '.join(media_src)}",
            f"object-src {' '.join(object_src)}",
        ]

        response['Content-Security-Policy'] = '; '.join(policy_parts)

        return response
