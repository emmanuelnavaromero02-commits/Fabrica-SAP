from __future__ import annotations

import re
from typing import Any

from fabrica.llm.simulated_text import title


def implementar(prompt: str, tier: str) -> dict[str, Any]:
    match = re.search(r"Objeto principal: (\S+)", prompt)
    name = match.group(1) if match else "Z_REQ"
    weak = tier in ("N1", "N2")
    select = "SELECT * FROM bkpf" if weak else "SELECT bukrs, belnr, budat FROM bkpf"
    code = (
        f"REPORT {name.lower()}.\n\n"
        "DATA gv_budat TYPE bkpf-budat.\n"
        "SELECT-OPTIONS s_budat FOR gv_budat.\n\n"
        "START-OF-SELECTION.\n"
        f"  {select}\n"
        "    WHERE budat IN @s_budat\n"
        "    INTO TABLE @DATA(lt_docs).\n"
    )
    if not weak:
        code += (
            "  cl_salv_table=>factory( IMPORTING r_salv_table = DATA(lo_alv)\n"
            "                          CHANGING  t_table      = lt_docs ).\n"
            "  lo_alv->display( ).\n"
        )
    return {
        "files": [{"path": f"src/{name.lower()}.prog.abap", "content": code}],
        "notes": "Borrador inicial" if weak else "Corregido con el paquete de relevo",
    }


def revisar(prompt: str, tier: str) -> dict[str, Any]:
    code = prompt.split("## Código", 1)[-1]
    issues = ["Evitar SELECT *: leer solo campos necesarios"] if "SELECT *" in code else []
    return {"approved": not issues, "objections": issues}


def documentar(prompt: str, tier: str) -> dict[str, Any]:
    return {"markdown": f"# Manual técnico — {title(prompt)}\n\nGenerado por la fábrica."}


HANDLERS = {"implementar": implementar, "revisar": revisar, "documentar": documentar}
