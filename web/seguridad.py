"""Acceso a la web: contraseña + código TOTP, bloqueo por IP, freno global y sesiones que caducan.

Bloqueo por IP (no global): un bot que pruebe contraseñas solo se bloquea a sí mismo.
Freno global: si hay muchos fallos en total (ataque desde muchas IP), cada intento
fallido tarda un poco más, pero nadie queda bloqueado: con la contraseña y el código
correctos se entra siempre.

La configuración (hash de la contraseña y secreto TOTP) vive fuera del repo, en
~/.config/farmaclaw/web.json con permisos 600. Se crea con `web/configurar.py`.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

import pyotp

CONFIG = Path(os.environ.get("FARMACLAW_WEB_CONFIG", Path.home() / ".config" / "farmaclaw" / "web.json"))

MAX_FALLOS = 5               # intentos fallidos seguidos desde una IP antes de bloquearla
BLOQUEO_S = 15 * 60          # duración del bloqueo de esa IP (y vida de su contador de fallos)
VENTANA_GLOBAL_S = 10 * 60   # freno global: fallos de todas las IP en los últimos 10 min…
UMBRAL_GLOBAL = 50           # …a partir de este número, cada fallo se retrasa
RETRASO_POR_FALLO_S = 0.1    # retraso creciente: 0,1 s por cada fallo por encima del umbral…
RETRASO_MAX_S = 5.0          # …hasta 5 s como máximo
MAX_IPS = 10_000             # máximo de IP con fallos registradas en memoria
INACTIVIDAD_S = 6 * 3600     # la sesión caduca tras 6 h sin uso (una jornada)
DURACION_MAX_S = 8 * 3600    # y, en cualquier caso, a las 8 h

# scrypt: lento a propósito para que probar contraseñas a ciegas sea caro.
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


def hash_contrasena(contrasena: str, sal: bytes | None = None) -> dict:
    sal = sal or secrets.token_bytes(16)
    h = hashlib.scrypt(contrasena.encode(), salt=sal, dklen=32, **_SCRYPT)
    return {"sal": sal.hex(), "hash": h.hex()}


def contrasena_valida(contrasena: str, guardado: dict) -> bool:
    calculado = hash_contrasena(contrasena, bytes.fromhex(guardado["sal"]))["hash"]
    return hmac.compare_digest(calculado, guardado["hash"])


def guardar_config(datos: dict, ruta: Path = CONFIG) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # Se crea con permisos 600 desde el principio (no hay un instante en que otros puedan leerlo).
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(datos, f)


def cargar_config(ruta: Path = CONFIG) -> dict:
    if not ruta.exists():
        raise SystemExit(f"No existe {ruta}. Créalo con: uv run python web/configurar.py")
    return json.loads(ruta.read_text())


log = logging.getLogger("farmaclaw-web")


class Guardia:
    """Comprueba credenciales y lleva las sesiones. Todo en memoria: al reiniciar, hay que volver a entrar."""

    def __init__(self, config: dict, reloj=time.time):
        self.config = config
        self.reloj = reloj
        # IP → {"fallos": seguidos, "hasta": fin del bloqueo, "ultimo": último fallo}
        self.por_ip: dict[str, dict] = {}
        self.fallos_globales: deque[float] = deque(maxlen=10 * UMBRAL_GLOBAL)  # instantes de los últimos fallos
        self.ultimo_paso_totp = -1   # un mismo código no vale dos veces
        self.sesiones: dict[str, dict] = {}
        self._cerrojo = threading.Lock()  # entrar() corre en un hilo aparte (scrypt es lento)
        self._ultima_limpieza = 0.0

    # ── Bloqueo por IP y freno global ──
    def _caducada(self, e: dict, ahora: float) -> bool:
        return ahora >= e["hasta"] and ahora - e["ultimo"] > BLOQUEO_S

    def limpiar(self, forzar: bool = False) -> None:
        """Quita las IP cuyo bloqueo y contador han caducado (como mucho una vez por minuto)."""
        ahora = self.reloj()
        if not forzar and ahora - self._ultima_limpieza < 60:
            return
        self._ultima_limpieza = ahora
        for ip in [ip for ip, e in self.por_ip.items() if self._caducada(e, ahora)]:
            del self.por_ip[ip]

    def bloqueado(self, ip: str) -> int:
        """Segundos que le faltan a esa IP para poder volver a intentarlo (0 si no está bloqueada)."""
        e = self.por_ip.get(ip)
        return max(0, int(e["hasta"] - self.reloj())) if e else 0

    def retraso(self) -> float:
        """Freno global: segundos que debe esperar la respuesta a un intento fallido (0 si hay pocos fallos)."""
        limite = self.reloj() - VENTANA_GLOBAL_S
        recientes = sum(1 for t in self.fallos_globales if t > limite)
        exceso = recientes - UMBRAL_GLOBAL
        return 0.0 if exceso <= 0 else min(RETRASO_MAX_S, exceso * RETRASO_POR_FALLO_S)

    def _anotar_fallo(self, ip: str) -> None:
        ahora = self.reloj()
        self.fallos_globales.append(ahora)
        self.limpiar()
        e = self.por_ip.get(ip)
        if e is None:
            if len(self.por_ip) >= MAX_IPS:
                self.limpiar(forzar=True)
            while len(self.por_ip) >= MAX_IPS:      # aún lleno: fuera la IP con el fallo más antiguo
                del self.por_ip[min(self.por_ip, key=lambda k: self.por_ip[k]["ultimo"])]
            e = self.por_ip[ip] = {"fallos": 0, "hasta": 0.0, "ultimo": ahora}
        if ahora - e["ultimo"] > BLOQUEO_S:          # fallos viejos: se empieza a contar de nuevo
            e["fallos"] = 0
        e["fallos"] += 1
        e["ultimo"] = ahora
        if e["fallos"] >= MAX_FALLOS:
            e["hasta"] = ahora + BLOQUEO_S
            e["fallos"] = 0
            log.warning("Acceso fallido desde %s: %d fallos seguidos, IP bloqueada %d min", ip, MAX_FALLOS, BLOQUEO_S // 60)
        else:
            log.warning("Acceso fallido desde %s (%d de %d)", ip, e["fallos"], MAX_FALLOS)

    def entrar(self, contrasena: str, codigo: str, ip: str = "desconocida") -> str | None:
        """Devuelve un token de sesión, o None si algo falla (sin decir qué, a propósito).
        Nunca se registran la contraseña ni el código."""
        if self.bloqueado(ip):
            log.warning("Intento desde %s, que está bloqueada", ip)
            return None
        ok_pass = contrasena_valida(contrasena or "", self.config["contrasena"])
        with self._cerrojo:
            ok_totp = self._totp_valido((codigo or "").strip())
            if not (ok_pass and ok_totp):
                self._anotar_fallo(ip)
                return None
            self.por_ip.pop(ip, None)                # acierto: esa IP empieza de cero
        token = secrets.token_urlsafe(32)
        ahora = self.reloj()
        self.sesiones[token] = {"inicio": ahora, "uso": ahora}
        return token

    def _totp_valido(self, codigo: str) -> bool:
        if not (codigo.isdigit() and len(codigo) == 6):
            return False
        totp = pyotp.TOTP(self.config["totp"])
        ahora = self.reloj()
        # Se admite el código del intervalo anterior y del siguiente (relojes algo desfasados).
        for desfase in (-1, 0, 1):
            paso = int(ahora // 30) + desfase
            if paso > self.ultimo_paso_totp and hmac.compare_digest(totp.at(paso * 30), codigo):
                self.ultimo_paso_totp = paso
                return True
        return False

    def sesion_valida(self, token: str | None) -> bool:
        s = self.sesiones.get(token or "")
        if not s:
            return False
        ahora = self.reloj()
        if ahora - s["uso"] > INACTIVIDAD_S or ahora - s["inicio"] > DURACION_MAX_S:
            self.sesiones.pop(token, None)
            return False
        s["uso"] = ahora
        return True

    def salir(self, token: str | None) -> None:
        self.sesiones.pop(token or "", None)
