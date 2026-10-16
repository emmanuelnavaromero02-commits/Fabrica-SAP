#!/usr/bin/env bash
# Instala las skills ABAP de la fábrica en el Claude Code del desarrollador
# (Mac/Linux). Las copia a ~/.claude/skills (personal skills) -> disponibles en
# todos los repos de requerimiento que el dev clone.
#
# Uso:
#   ./install-skills.sh            # instala/actualiza ambas (pregunta si existe)
#   ./install-skills.sh --force    # sobreescribe sin preguntar
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.claude/skills"
SKILLS=(sap-abap-classic sap-abap-cloud)
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

mkdir -p "$DEST"
echo "Instalando skills en: $DEST"

for s in "${SKILLS[@]}"; do
  if [[ ! -f "$SRC/$s/SKILL.md" ]]; then
    echo "  aviso: no encontre $s/SKILL.md — omito."; continue
  fi
  if [[ -d "$DEST/$s" && $FORCE -eq 0 ]]; then
    read -r -p "  '$s' ya existe. ¿Sobreescribir? (s/N) " r
    [[ "$r" =~ ^[sSyY]$ ]] || { echo "  omitido: $s"; continue; }
  fi
  rm -rf "$DEST/$s"
  cp -R "$SRC/$s" "$DEST/$s"
  echo "  instalado: $s"
done

echo
echo "Listo. Abre tu repo de requerimiento con Claude Code y verifica con /skills."
