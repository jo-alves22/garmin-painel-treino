"""Cliente da Garmin Personal API (fluxo de token + GETs).

Porta, em Python, do fluxo validado em ../verify_garmin_api.sh:
  1. POST .../personal-api-key/token  com header `Authorization: ApiKey <key>`
     -> o JWT vem como TEXTO PURO no corpo (tem claim `exp`).
  2. GET <path>  com header `Authorization: Bearer <jwt>`.

A api_Key é lida da variável de ambiente GARMIN_API_KEY (carregada de ../.env, veja
../.env.example). O token (JWT) é cacheado em memória e em
~/.config/garmin-painel/bearer.json, renovado quando perto de expirar.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"

_APP_DIR = Path(__file__).resolve().parent
_CACHE_FILE = Path.home() / ".config" / "garmin-painel" / "bearer.json"
_REFRESH_WINDOW_S = 60 * 60  # renova se faltar menos de 1h

load_dotenv(_APP_DIR.parent / ".env")


class GarminError(RuntimeError):
    pass


def _read_api_key() -> str:
    key = os.getenv("GARMIN_API_KEY")
    if not key:
        raise GarminError(
            "GARMIN_API_KEY não definida. Copie .env.example para .env e preencha a api_Key."
        )
    return key.strip()


def _jwt_exp(token: str) -> float:
    """Lê o claim `exp` do JWT (sem validar assinatura)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception as exc:  # noqa: BLE001
        raise GarminError("Token do endpoint não parece um JWT com claim exp") from exc


class GarminClient:
    """Mantém um bearer token válido e expõe GETs à Personal API."""

    def __init__(self, session: requests.Session | None = None, timeout: float = 30.0) -> None:
        self._session = session or requests.Session()
        self._timeout = timeout
        self._token: str | None = None
        self._exp: float = 0.0

    # ---- token -----------------------------------------------------------
    def _load_cached(self) -> None:
        try:
            cache = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            self._token, self._exp = cache["access_token"], float(cache["expires_at"])
        except (FileNotFoundError, KeyError, ValueError):
            self._token, self._exp = None, 0.0

    def _store_cached(self) -> None:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"access_token": self._token, "expires_at": self._exp}),
            encoding="utf-8",
        )
        _CACHE_FILE.chmod(0o600)

    def _ensure_token(self) -> str:
        if self._token is None:
            self._load_cached()
        if self._token and self._exp - time.time() > _REFRESH_WINDOW_S:
            return self._token
        # Trocar a api_Key por um token novo.
        resp = self._session.post(
            f"{BASE_URL}{TOKEN_PATH}",
            headers={"Accept": "*/*", "Authorization": f"ApiKey {_read_api_key()}"},
            timeout=self._timeout,
        )
        if not resp.ok:
            raise GarminError(f"Falha ao obter token (HTTP {resp.status_code}): {resp.text[:200]}")
        self._token = resp.text.strip()
        self._exp = _jwt_exp(self._token)
        self._store_cached()
        return self._token

    def token_expires_at(self) -> float:
        self._ensure_token()
        return self._exp

    # ---- requests --------------------------------------------------------
    def get(self, path: str, params: dict | None = None):
        """GET um path da API e devolve JSON decodificado (ou None se vazio)."""
        resp = self._session.get(
            f"{BASE_URL}{path}",
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._ensure_token()}"},
            params=params,
            timeout=self._timeout,
        )
        if resp.status_code == 204 or not resp.content:
            return None
        if not resp.ok:
            raise GarminError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()
