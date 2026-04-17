"""
security_headers.py — Middleware que agrega cabeceras de seguridad a todas las
respuestas.

CSP: elegimos una política realista dado que el frontend usa `onclick=` inline
y `style=` inline (refactor a addEventListener quedaría para otro sprint). Aun
con 'unsafe-inline' seguimos bloqueando scripts externos no permitidos e
iframes, que es la protección principal contra XSS vía terceros y clickjacking.

HSTS: solo se setea si la request llegó por HTTPS (no queremos romper local
http://).
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])


_STATIC_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for k, v in _STATIC_HEADERS.items():
            response.headers.setdefault(k, v)
        # HSTS solo en HTTPS (Render termina TLS y setea x-forwarded-proto=https).
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response
