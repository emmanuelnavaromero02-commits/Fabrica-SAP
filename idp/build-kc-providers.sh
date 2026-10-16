#!/usr/bin/env bash
# Empaqueta los script mappers de idp/kc-script-mapper/ en un JAR provider de
# Keycloak en idp/kc-providers/op-admin-mapper.jar (montado en
# /opt/keycloak/providers/). El JAR es un ZIP con el/los .js en la raíz y
# META-INF/keycloak-scripts.json. Tras (re)construirlo hay que recrear el
# contenedor de Keycloak para que lo augmente (build) al arrancar.
#
# Uso (en el host de AWS o local, desde la raíz del repo):
#   bash idp/build-kc-providers.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/kc-script-mapper"
OUT="$HERE/kc-providers"
JAR="$OUT/op-admin-mapper.jar"
mkdir -p "$OUT" "$SRC/META-INF"
cp "$SRC/keycloak-scripts.json" "$SRC/META-INF/keycloak-scripts.json"
rm -f "$JAR"
# ZIP con META-INF/keycloak-scripts.json + el/los .js en la raíz.
( cd "$SRC" && zip -q -r "$JAR" META-INF/keycloak-scripts.json op-admin-mapper.js )
echo "OK -> $JAR"
unzip -l "$JAR" || true
