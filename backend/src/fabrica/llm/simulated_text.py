from __future__ import annotations

import re


def slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", text.upper())[:20].strip("_") or "REQ"


def title(prompt: str) -> str:
    match = re.search(r"Título: (.+)", prompt)
    return match.group(1).strip() if match else "Requisito"
