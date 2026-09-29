from __future__ import annotations

import re
from typing import Any


def estimar(prompt: str, tier: str) -> dict[str, Any]:
    names = re.findall(r'"name": "([^"]+)"', prompt) or ["Z_REQ"]
    return {
        "items": [
            {"object": n, "size": "M", "rationale": "Reporte con selección y ALV"} for n in names
        ],
        "assumptions": ["Datos maestros disponibles en DEV"],
    }


HANDLERS = {"estimar": estimar}
