"""Phase G.3.6 — Telegram photo/image download helpers."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_IMAGE_MIMES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp",
})


def is_image_message(message: dict[str, Any]) -> bool:
    if message.get("photo"):
        return True
    doc = message.get("document") or {}
    mime = str(doc.get("mime_type") or "").lower()
    if mime.startswith("image/") or mime in _IMAGE_MIMES:
        return True
    name = str(doc.get("file_name") or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def _image_filename(message: dict[str, Any]) -> str:
    doc = message.get("document") or {}
    if doc.get("file_name"):
        return str(doc["file_name"])
    return "chart.jpg"


def download_telegram_image(
    message: dict[str, Any],
    *,
    token: str,
    dest_dir: Path | None = None,
) -> tuple[str, str, str]:
    """Download Telegram photo/document image. Returns (path, base64, filename)."""
    if message.get("photo"):
        file_id = message["photo"][-1]["file_id"]
    elif message.get("document"):
        file_id = message["document"]["file_id"]
    else:
        raise ValueError("message has no image")

    file_path = _telegram_file_path(token, file_id)
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    raw = resp.content

    filename = _image_filename(message)
    out_dir = dest_dir or Path("data/telegram_vision_g36")
    out_dir.mkdir(parents=True, exist_ok=True)
    msg_id = int(message.get("message_id") or 0)
    suffix = Path(filename).suffix or ".jpg"
    out_path = out_dir / f"tg_{msg_id}{suffix}"
    out_path.write_bytes(raw)

    b64 = base64.standard_b64encode(raw).decode("ascii")
    return str(out_path), b64, filename


def _telegram_file_path(token: str, file_id: str) -> str:
    url = f"https://api.telegram.org/bot{token}/getFile"
    resp = requests.get(url, params={"file_id": file_id}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram getFile failed")
    path = data.get("result", {}).get("file_path")
    if not path:
        raise RuntimeError("Telegram file_path missing")
    return str(path)
