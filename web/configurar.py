"""Crea (o renueva) la contraseña y el código del móvil para entrar en la web.

Ejecútalo tú en la terminal de la VM; la contraseña no pasa por ningún otro sitio:
    uv run python web/configurar.py

Guarda en ~/.config/farmaclaw/web.json (permisos 600) el hash de la contraseña y
el secreto TOTP, y muestra un QR para añadirlo a la app de códigos del iPhone.
Volver a ejecutarlo invalida la contraseña y el código anteriores.
"""

import getpass
import secrets
import string
import sys
from pathlib import Path

import pyotp
import segno

sys.path.insert(0, str(Path(__file__).parent))
import seguridad  # noqa: E402

MIN_LONGITUD = 14


def pedir_contrasena() -> str:
    print(f"Elige una contraseña de al menos {MIN_LONGITUD} caracteres (no se verá al escribir).")
    sugerida = "-".join("".join(secrets.choice(string.ascii_lowercase) for _ in range(5)) for _ in range(4))
    print(f"Si quieres, usa esta generada al azar: {sugerida}")
    while True:
        c1 = getpass.getpass("Contraseña: ")
        if len(c1) < MIN_LONGITUD:
            print(f"Demasiado corta: mínimo {MIN_LONGITUD} caracteres.")
            continue
        if getpass.getpass("Repítela: ") != c1:
            print("No coinciden, vuelve a probar.")
            continue
        return c1


def main():
    if seguridad.CONFIG.exists():
        r = input(f"Ya existe {seguridad.CONFIG}. ¿Sustituir contraseña y código? (s/N) ")
        if r.strip().lower() != "s":
            return
    contrasena = pedir_contrasena()
    secreto = pyotp.random_base32()
    seguridad.guardar_config({"contrasena": seguridad.hash_contrasena(contrasena), "totp": secreto})

    uri = pyotp.TOTP(secreto).provisioning_uri(name="farmaclaw", issuer_name="Farmaclaw")
    print("\nEscanea este QR con la cámara del iPhone (o con Google/Microsoft Authenticator):\n")
    segno.make(uri, error="m").terminal(compact=True)
    print("\nSi el QR no se lee bien, añádelo a mano con esta clave de configuración:")
    print(f"    {secreto}")
    print("\nComprueba que funciona:")
    while True:
        codigo = input("Escribe el código de 6 cifras que muestra el móvil (o Intro para saltar): ").strip()
        if not codigo:
            break
        if pyotp.TOTP(secreto).verify(codigo, valid_window=1):
            print("Código correcto.")
            break
        print("No coincide. Espera al siguiente código y vuelve a probar.")
    print(f"\nGuardado en {seguridad.CONFIG}. Reinicia la web para aplicarlo:")
    print("    systemctl --user restart farmaclaw-web")


if __name__ == "__main__":
    main()
