"""Google Drive サービスアカウント認証。

Cloud（Streamlit Secrets）では [gcp_service_account] を使う。
固定フォルダ 1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9 を
サービスアカウントの client_email に「閲覧者」で共有すること。

client_email 想定:
  vr-ai-analysis-drive-reader@routinesupport.iam.gserviceaccount.com
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

EXPECTED_SERVICE_ACCOUNT_EMAIL = (
    "vr-ai-analysis-drive-reader@routinesupport.iam.gserviceaccount.com"
)

_token_holder: dict[str, Any] = {"creds": None, "fp": "", "last_error": None}


def _section_to_plain_dict(section: Any) -> dict[str, Any]:
    """Streamlit AttrDict / 通常 dict を素の dict[str, str|Any] に変換。"""
    out: dict[str, Any] = {}
    try:
        keys = list(section.keys())
    except Exception:
        if isinstance(section, dict):
            keys = list(section.keys())
        else:
            return out
    for k in keys:
        try:
            v = section[k]
        except Exception:
            continue
        if hasattr(v, "keys") and not isinstance(v, (str, bytes)):
            out[str(k)] = _section_to_plain_dict(v)
        else:
            out[str(k)] = v
    return out


def _from_streamlit_secrets() -> dict[str, Any] | None:
    try:
        import streamlit as st

        secrets = st.secrets  # type: ignore[attr-defined]
    except Exception:
        return None

    try:
        keys = list(secrets.keys())
    except Exception:
        return None

    try:
        if "gcp_service_account" in keys:
            section = secrets["gcp_service_account"]
            # セクション全体が JSON 文字列の場合
            if isinstance(section, str):
                return json.loads(section)
            return _section_to_plain_dict(section)
        if "GOOGLE_SERVICE_ACCOUNT_JSON" in keys:
            raw = secrets.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
            if raw:
                return json.loads(raw) if isinstance(raw, str) else _section_to_plain_dict(raw)
    except Exception as e:
        logger.warning("failed to read service account from secrets: %s", e)
        _token_holder["last_error"] = f"secrets読み込み失敗: {e}"
    return None


def _from_env_or_file() -> dict[str, Any] | None:
    raw = (os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip()
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("invalid GOOGLE_SERVICE_ACCOUNT_JSON env: %s", e)
            _token_holder["last_error"] = f"GOOGLE_SERVICE_ACCOUNT_JSON 不正: {e}"

    path = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if path and Path(path).is_file():
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("failed to read GOOGLE_APPLICATION_CREDENTIALS: %s", e)
            _token_holder["last_error"] = f"credentialsファイル読込失敗: {e}"
    return None


def _normalize_private_key(info: dict[str, Any]) -> dict[str, Any]:
    """Streamlit TOML / Cloud Secrets 由来の private_key を PEM として使える形に直す。

    Cloud で多い失敗:
      - `\\n` が実改行にならない
      - base64 本体に不正文字（例: `.` = symbol 46）が混入
    """
    import base64
    import re

    out = {str(k): v for k, v in info.items()}
    key = str(out.get("private_key", ""))
    key = key.strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        key = key[1:-1].strip()

    # エスケープ改行を実改行へ（多重エスケープも解消）
    for _ in range(5):
        if "\\n" in key:
            key = key.replace("\\n", "\n")
        else:
            break
    key = key.replace("\\r", "").replace("\r\n", "\n").replace("\r", "\n")
    key = key.strip()

    # BEGIN/END ブロックを抽出（PKCS#8 / RSA 両対応）
    m = re.search(
        r"-----BEGIN ([A-Z0-9 ]*PRIVATE KEY)-----(.*?)-----END \1-----",
        key,
        flags=re.DOTALL,
    )
    if not m:
        # ラベル不一致でも BEGIN/END があれば salvage
        m2 = re.search(
            r"-----BEGIN ([^-]+)-----(.*?)-----END ([^-]+)-----",
            key,
            flags=re.DOTALL,
        )
        if not m2:
            msg = "private_key から PEM BEGIN/END ブロックを抽出できません"
            logger.warning(msg)
            _token_holder["last_error"] = msg
            out["private_key"] = key + ("\n" if not key.endswith("\n") else "")
            return out
        label = m2.group(1).strip()
        body = m2.group(2)
    else:
        label = m.group(1).strip()
        body = m.group(2)

    # base64 として不正な文字（`.`=46 など）と空白を除去
    cleaned = re.sub(r"[^A-Za-z0-9+/=]", "", body)
    removed = len(re.sub(r"\s+", "", body)) - len(cleaned)
    if removed > 0:
        logger.warning(
            "private_key base64 から不正文字を %s 文字除去しました（Invalid symbol 46 等の原因）",
            removed,
        )

    # base64 として decode → encode し直して検証
    try:
        # padding 補正
        pad = (-len(cleaned)) % 4
        if pad:
            cleaned += "=" * pad
        raw = base64.b64decode(cleaned, validate=False)
        cleaned = base64.b64encode(raw).decode("ascii")
    except Exception as e:
        msg = f"private_key base64 の検証に失敗: {type(e).__name__}: {e}"
        logger.warning(msg)
        _token_holder["last_error"] = msg

    # 64文字折り返しで正規 PEM を再構築
    lines = [cleaned[i : i + 64] for i in range(0, len(cleaned), 64)]
    pem = "-----BEGIN " + label + "-----\n"
    pem += "\n".join(lines)
    if lines:
        pem += "\n"
    pem += "-----END " + label + "-----\n"
    out["private_key"] = pem

    for field in (
        "type",
        "project_id",
        "private_key_id",
        "client_email",
        "client_id",
        "token_uri",
        "auth_uri",
        "client_x509_cert_url",
        "auth_provider_x509_cert_url",
        "universe_domain",
    ):
        if field in out and out[field] is not None:
            out[field] = str(out[field]).strip()

    if not out.get("type"):
        out["type"] = "service_account"
    if not out.get("token_uri"):
        out["token_uri"] = "https://oauth2.googleapis.com/token"

    # 診断ログ（秘密本体は出さない）
    logger.info(
        "private_key normalized: label=%s pem_len=%s has_newline=%s client_email=%s",
        label,
        len(pem),
        "\n" in pem,
        out.get("client_email"),
    )
    return out


def load_service_account_info() -> dict[str, Any] | None:
    """サービスアカウント JSON（dict）を返す。無ければ None。"""
    info = _from_streamlit_secrets() or _from_env_or_file()
    if not info:
        return None
    if not info.get("client_email") or not info.get("private_key"):
        msg = "service account info missing client_email or private_key"
        logger.warning(msg)
        _token_holder["last_error"] = msg
        return None
    return _normalize_private_key(info)


def get_service_account_email() -> str | None:
    info = load_service_account_info()
    if not info:
        return None
    email = str(info.get("client_email") or "").strip()
    return email or None


def get_last_auth_error() -> str | None:
    err = _token_holder.get("last_error")
    return str(err) if err else None


def diagnose_service_account() -> dict[str, Any]:
    """UI / ログ用の診断情報（秘密情報は出さない）。"""
    info = load_service_account_info()
    if not info:
        return {
            "ok": False,
            "has_info": False,
            "error": get_last_auth_error() or "Secrets に [gcp_service_account] がありません",
        }
    key = str(info.get("private_key", ""))
    return {
        "ok": False,
        "has_info": True,
        "client_email": info.get("client_email"),
        "has_private_key": bool(key),
        "private_key_has_begin": "BEGIN PRIVATE KEY" in key,
        "private_key_has_end": "END PRIVATE KEY" in key,
        "private_key_has_newline": "\n" in key.strip(),
        "private_key_len": len(key),
        "has_token_uri": bool(info.get("token_uri")),
        "error": get_last_auth_error(),
    }


def get_drive_access_token(*, force_refresh: bool = False) -> str | None:
    """サービスアカウントで Drive readonly の access token を取得。"""
    info = load_service_account_info()
    if not info:
        return None

    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account
    except ImportError as e:
        msg = f"google-auth 未インストール: {e}"
        logger.error(msg)
        _token_holder["last_error"] = msg
        return None

    fp = f"{info.get('client_email')}:{info.get('private_key_id')}:{len(str(info.get('private_key')))}"
    creds = _token_holder.get("creds")
    if force_refresh or _token_holder.get("fp") != fp or creds is None:
        try:
            creds = service_account.Credentials.from_service_account_info(
                info,
                scopes=[DRIVE_READONLY_SCOPE],
            )
        except Exception as e:
            msg = f"Credentials 作成失敗（private_key形式を確認）: {type(e).__name__}: {e}"
            logger.warning(msg)
            _token_holder["last_error"] = msg
            _token_holder["creds"] = None
            return None
        _token_holder["creds"] = creds
        _token_holder["fp"] = fp

    try:
        if force_refresh or not getattr(creds, "valid", False) or not creds.token:
            creds.refresh(GoogleAuthRequest())
        elif getattr(creds, "expired", False):
            creds.refresh(GoogleAuthRequest())
    except Exception as e:
        msg = f"token refresh 失敗: {type(e).__name__}: {e}"
        logger.warning(msg)
        _token_holder["last_error"] = msg
        # 壊れた鍵キャッシュを捨てて次回再構築
        _token_holder["creds"] = None
        _token_holder["fp"] = ""
        return None

    token = getattr(creds, "token", None)
    if token:
        _token_holder["last_error"] = None
        logger.info("drive service account auth ok email=%s", info.get("client_email"))
    else:
        _token_holder["last_error"] = "token が空です"
    return token


def auth_status() -> dict[str, Any]:
    """画面表示用の認証状態。"""
    diag = diagnose_service_account()
    email = get_service_account_email()
    token_ok = False
    error = diag.get("error")
    if email:
        try:
            token_ok = bool(get_drive_access_token())
            error = get_last_auth_error()
        except Exception as e:
            error = str(e)
            _token_holder["last_error"] = error
    return {
        "configured": bool(email),
        "client_email": email,
        "expected_email": EXPECTED_SERVICE_ACCOUNT_EMAIL,
        "email_matches_expected": (
            email == EXPECTED_SERVICE_ACCOUNT_EMAIL if email else False
        ),
        "token_ok": token_ok,
        "error": error,
        "diagnosis": diag,
        "folder_id": "1ClTITbRVQc_hiDDIF5lfEEEttJs5qTc9",
    }
