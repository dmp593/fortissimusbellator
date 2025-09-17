class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # One-time configuration and initialization.

    def __call__(self, request):
        # Code to be executed for each request before
        # the view (and later middleware) are called.

        response = self.get_response(request)

        # Build a Content-Security-Policy header based on external hosts used
        # in our templates. Adjust these lists if you add/remove third-party
        # embeds or CDNs.
        self_src = ["'self'"]

        # Script sources: local scripts, inline for small injected snippets,
        # and common CDNs / embeds (tawk, jsdelivr)
        script_src = self_src + [
            "'unsafe-inline'",
            "'unsafe-eval'",  # Needed in production (mod_pagespeed)
            'https://cdn.jsdelivr.net',
            '*.google.com',
            '*.gstatic.com',
            '*.tawk.to',
        ]

        # Styles: allow Google Fonts and inline styles used by some components
        style_src = self_src + [
            "'unsafe-inline'",
            'https://fonts.googleapis.com',
        ]

        # Fonts: Google Fonts host
        font_src = self_src + ['https://fonts.gstatic.com']

        # Images: self and data URIs; allow social thumbnails and CDN images
        img_src = self_src + [
            'data:',
            'https://www.facebook.com',
            'https://www.instagram.com',
            'https://cdn.jsdelivr.net',
            '*.openstreetmap.org',
        ]

        # Connections (XHR, WebSocket): allow same-origin and tawk endpoints
        connect_src = self_src + [
            '*.tawk.to',
        ]

        # Frames: none by default (prevents embedding)
        frame_src = self_src + [
            '*.google.com',
        ]

        # Other directives
        media_src = self_src
        object_src = ["'none'"]

        policy_parts = [
            f"default-src {' '.join(self_src)}",
            f"script-src {' '.join(script_src)}",
            f"style-src {' '.join(style_src)}",
            f"font-src {' '.join(font_src)}",
            f"img-src {' '.join(img_src)}",
            f"connect-src {' '.join(connect_src)}",
            f"frame-ancestors {' '.join(frame_src)}",
            f"media-src {' '.join(media_src)}",
            f"object-src {' '.join(object_src)}",
        ]

        response['Content-Security-Policy'] = '; '.join(policy_parts)

        return response
