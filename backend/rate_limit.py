"""
rate_limit.py — Sliding-window in-memory rate limiter.

Para instancias múltiples habría que mover esto a Redis. Mientras tanto, en
Render (un solo dyno) + LAN con .exe, basta con memoria local.

Uso típico:
    from rate_limit import login_limiter
    login_limiter.check_or_raise(request, username)

Diseño:
- Ventana deslizante: guarda timestamps de cada intento reciente.
- Clave compuesta: (username_lower, ip) para que no alcance con rotar IP
  ni con rotar username. 429 si cualquiera de las dos claves se excede.
- Auto-GC al llegar a un umbral de claves.
"""
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int, gc_threshold: int = 1000):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._gc_threshold = gc_threshold

    def _prune(self, bucket: Deque[float], now: float) -> None:
        cutoff = now - self.window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def _gc(self, now: float) -> None:
        cutoff = now - self.window
        stale = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
        for k in stale:
            self._buckets.pop(k, None)

    def register_attempt(self, key: Tuple[str, str]) -> int:
        """Registra un intento y devuelve cuántos hay en la ventana."""
        now = monotonic()
        with self._lock:
            if len(self._buckets) > self._gc_threshold:
                self._gc(now)
            bucket = self._buckets[key]
            self._prune(bucket, now)
            bucket.append(now)
            return len(bucket)

    def count(self, key: Tuple[str, str]) -> int:
        now = monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return 0
            self._prune(bucket, now)
            return len(bucket)

    def retry_after(self, key: Tuple[str, str]) -> int:
        """Segundos hasta que el bucket se desbloquee (0 si ya está libre)."""
        now = monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return 0
            self._prune(bucket, now)
            if len(bucket) < self.max_attempts:
                return 0
            return max(1, int(self.window - (now - bucket[0])))

    def reset(self, key: Tuple[str, str]) -> None:
        """Limpia el bucket (típicamente después de un login exitoso)."""
        with self._lock:
            self._buckets.pop(key, None)


class LoginRateLimiter:
    """
    Rate limit combinado para /auth/login:
    - max_attempts por (username, ip)
    - max_attempts_per_ip por IP a secas (protege contra password spraying)

    Ambos usan la misma ventana temporal.
    """
    def __init__(self, max_attempts: int = 5, max_attempts_per_ip: int = 20, window_seconds: int = 900):
        self._per_user = SlidingWindowLimiter(max_attempts, window_seconds)
        self._per_ip = SlidingWindowLimiter(max_attempts_per_ip, window_seconds)

    @staticmethod
    def _keys(request: Request, username: str) -> tuple[tuple[str, str], tuple[str, str]]:
        ip = _client_ip(request)
        u = (username or "").strip().lower()
        return (u, ip), ("*", ip)

    def check_or_raise(self, request: Request, username: str) -> None:
        """Lanza 429 si se excedieron los intentos antes de validar la password."""
        user_key, ip_key = self._keys(request, username)
        retry = max(self._per_user.retry_after(user_key), self._per_ip.retry_after(ip_key))
        if retry > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Demasiados intentos. Probá de nuevo en {retry}s.",
                headers={"Retry-After": str(retry)},
            )

    def register_failure(self, request: Request, username: str) -> None:
        user_key, ip_key = self._keys(request, username)
        self._per_user.register_attempt(user_key)
        self._per_ip.register_attempt(ip_key)

    def register_success(self, request: Request, username: str) -> None:
        user_key, _ = self._keys(request, username)
        # Reseteo solo la clave user+ip; dejo la clave por IP para no dar free pass
        # a un atacante que conoce 1 credencial pero sigue pinchando otras.
        self._per_user.reset(user_key)


# Singleton compartido para /auth/login. 5 intentos fallidos cada 15 min por
# (usuario, IP) — después hay que esperar. 20 por IP global para cortar spraying.
login_limiter = LoginRateLimiter(max_attempts=5, max_attempts_per_ip=20, window_seconds=900)
