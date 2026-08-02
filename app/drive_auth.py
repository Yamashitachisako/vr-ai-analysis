"""Google Drive サービスアカウント認証。

Cloud（Streamlit Secrets）では [gcp_service_account] を使う。
固定フォルダ 1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9 を
サービスアカウントの client_email に「閲覧者」で共有すること。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

_token_holder: dict[str, Any] = {"creds": None, "fp": ""}


def _from_streamlit_secrets() -> dict[str, Any] | None:
    try:
        import streamlit as st

        secrets = st.secrets  # type: ignore[attr-defined]
    except Exception:
        return None

    try:
        # secrets.toml が無いとここで失敗する
        keys = list(secrets.keys())
    except Exception:
        return None

    try:
        if "gcp_service_account" in keys:
            return dict(secrets["gcp_service_account"])
        if "GOOGLE_SERVICE_ACCOUNT_JSON" in keys:
            raw = secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
            if raw:
                return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception as e:
        logger.warning("failed to read service account from secrets: %s", e)
    return None


def _from_env_or_file() -> dict[str, Any] | None:
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("invalid GOOGLE_SERVICE_ACCOUNT_JSON env: %s", e)

    path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if path and Path(path).is_file():
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("failed to read GOOGLE_APPLICATION_CREDENTIALS: %s", e)
    return None


def _normalize_private_key(info: dict[str, Any]) -> dict[str, Any]:
    """Streamlit TOML 由来の \\n を実改行に直す。"""
    key = str(info.get("private_key", ""))
    if "\\n" in key:
        out = dict(info)
        out["private_key"] = key.replace("\\n", "\n")
        return out
    return info


def load_service_account_info() -> dict[str, Any] | None:
    """サービスアカウント JSON（dict）を返す。無ければ None。"""
    info = _from_streamlit_secrets() or _from_env_or_file()
    if not info:
        return None
    if not info.get("client_email") or not info.get("private_key"):
        logger.warning("service account info missing client_email or private_key")
        return None
    return _normalize_private_key(info)


def get_service_account_email() -> str | None:
    info = load_service_account_info()
    if not info:
        return None
    email = str(info.get("client_email") or "").strip()
    return email or None


def get_drive_access_token(*, force_refresh: bool = False) -> str | None:
    """サービスアカウントで Drive readonly の access token を取得。"""
    info = load_service_account_info()
    if not info:
        return None

    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account
    except ImportError as e:
        logger.error(
            "google-auth が未インストールです。requirements.txt に google-auth を追加してください: %s",
            e,
        )
        return None

    fp = f"{info.get('client_email')}:{info.get('private_key_id')}"
    creds = _token_holder.get("creds")
    if force_refresh or _token_holder.get("fp") != fp or creds is None:
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=[DRIVE_READONLY_SCOPE],
        )
        _token_holder["creds"] = creds
        _token_holder["fp"] = fp

    try:
        if force_refresh or not getattr(creds, "valid", False) or not creds.token:
            creds.refresh(GoogleAuthRequest())
        elif getattr(creds, "expired", False):
            creds.refresh(GoogleAuthRequest())
    except Exception as e:
        logger.warning("service account token refresh failed: %s", e)
        return None

    token = getattr(creds, "token", None)
    if token:
        logger.info("drive service account auth ok email=%s", info.get("client_email"))
    return token


def auth_status() -> dict[str, Any]:
    """画面表示用の認証状態。"""
    email = get_service_account_email()
    token_ok = False
    error = None
    if email:
        try:
            token_ok = bool(get_drive_access_token())
        except Exception as e:
            error = str(e)
    return {
        "configured": bool(email),
        "client_email": email,
        "token_ok": token_ok,
        "error": error,
    }
