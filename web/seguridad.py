"""Acceso a la web: contraseña + código TOTP, bloqueo por intentos y sesiones que caducan.

La configuración (hash de la contraseña y secreto TOTP) vive fuera del repo, en
~/.config/farmaclaw/web.json con permisos 600. Se crea con `web/configurar.py`.
"""

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import pyotp

CONFIG = Path(os.environ.get("FARMACLAW_WEB_CONFIG", Path.home() / ".config" / "farmaclaw" / "web.json"))

MAX_FALLOS = 5               # intentos fallidos seguidos antes de bloquear
BLOQUEO_S = 15 * 60          # duración del bloqueo
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


class Guardia:
    """Comprueba credenciales y lleva las sesiones. Todo en memoria: al reiniciar, hay que volver a entrar."""

    def __init__(self, config: dict, reloj=time.time):
        self.config = config
        self.reloj = reloj
        self.fallos = 0
        self.bloqueado_hasta = 0.0
        self.ultimo_paso_totp = -1   # un mismo código no vale dos veces
        self.sesiones: dict[str, dict] = {}

    def bloqueado(self) -> int:
        """Segundos que faltan para poder volver a intentarlo (0 si no hay bloqueo)."""
        return max(0, int(self.bloqueado_hasta - self.reloj()))

    def entrar(self, contrasena: str, codigo: str) -> str | None:
        """Devuelve un token de sesión, o None si algo falla (sin decir qué, a propósito)."""
        if self.bloqueado():
            return None
        ok_pass = contrasena_valida(contrasena or "", self.config["contrasena"])
        ok_totp = self._totp_valido((codigo or "").strip())
        if not (ok_pass and ok_totp):
            self.fallos += 1
            if self.fallos >= MAX_FALLOS:
                self.bloqueado_hasta = self.reloj() + BLOQUEO_S
                self.fallos = 0
            return None
        self.fallos = 0
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
