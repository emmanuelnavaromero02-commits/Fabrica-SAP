#!/bin/bash
# Watchdog de túneles VPN (cron cada ~2 min, root en el host). Por sistema:
#  - KEEPALIVE: si está arriba, toca el SAP (curl) para que el FortiGate no lo
#    deje en idle y lo tire.
#  - AUTO-RECONEXIÓN (solo sistemas SIN OTP, auto=1): si el proceso openfortivpn
#    murió DE VERDAD, re-levanta con `vpnctl up` + `otp` dummy (re-agrega la
#    ruta). Los de OTP (auto=0) NO se re-levantan (necesitan código): solo DOWN.
#
# NO DESTRUCTIVO: la "vitalidad" se mide por el PROCESO openfortivpn real
# (pgrep sobre su .conf), NO por `vpnctl status` (que dependía del pidfile y
# fallaba). Si el proceso vive, JAMÁS se reinicia el túnel: a lo sumo, tras
# N fallos SEGUIDOS del keepalive, se re-agrega la ruta (`vpnctl reroute`,
# sin tocar el proceso). Un timeout suelto del SAP nunca tumba la VPN.
#
# Se invoca vpnctl vía `bash` a propósito: funciona aunque el archivo pierda
# el bit de ejecución (bug histórico que dejaba el auto-reconnect inerte).
#
# Mapa hardcoded (no secreto), separado por "|": "<sys>|<auto 0/1>|<keepalive_url>".
# (La config con credenciales sigue en SSM /fabrica-sw/vpn/<sys>, la lee vpnctl.)
set -uo pipefail
export HOME=/root
VPNCTL="/opt/fabrica/infra/scripts/vpn/vpnctl.sh"
LOG=/run/vpn/watchdog.log
KA_THRESHOLD=3          # fallos seguidos de keepalive antes de re-rutear
mkdir -p /run/vpn

WATCH="
BETA|1|https://10.20.46.6:44300/sap/public/ping
TAILOY|0|https://10.50.23.186:44300/sap/public/ping
"

_ts() { date '+%F %T'; }
_ka_ok() { [ -n "$1" ] && curl -k -m 6 -sf -o /dev/null "$1" 2>/dev/null; }
_proc_alive() { pgrep -f "openfortivpn -c /run/vpn/$1.conf" >/dev/null 2>&1; }
_kaf()     { echo "/run/vpn/$1.kafail"; }
_kaf_get() { cat "$(_kaf "$1")" 2>/dev/null || echo 0; }
_kaf_set() { echo "${2:-0}" > "$(_kaf "$1")"; }

_recover() {            # SOLO cuando el proceso murió de verdad
  bash "$VPNCTL" up "$1" >/dev/null 2>&1
  sleep 5
  bash "$VPNCTL" otp "$1" 000000 >/dev/null 2>&1
}
_reroute() { bash "$VPNCTL" reroute "$1" >/dev/null 2>&1; }

printf '%s\n' "$WATCH" | while IFS='|' read -r sys auto ka; do
  [ -z "$sys" ] && continue
  if _proc_alive "$sys"; then
    # Túnel VIVO: nunca reiniciar. A lo sumo re-rutear si el keepalive falla repetido.
    if _ka_ok "$ka"; then
      _kaf_set "$sys" 0                              # sano
    else
      n=$(( $(_kaf_get "$sys") + 1 )); _kaf_set "$sys" "$n"
      echo "$(_ts) $sys vivo pero keepalive falla ($n/$KA_THRESHOLD)" >> "$LOG"
      if [ "$n" -ge "$KA_THRESHOLD" ]; then
        echo "$(_ts) $sys -> reroute (sin reiniciar)" >> "$LOG"
        _reroute "$sys"; _kaf_set "$sys" 0
      fi
    fi
  else
    # Proceso MUERTO de verdad.
    _kaf_set "$sys" 0
    if [ "$auto" = "1" ]; then
      echo "$(_ts) $sys DOWN (proceso muerto) -> re-up auto" >> "$LOG"
      _recover "$sys"
    else
      echo "$(_ts) $sys DOWN (proceso muerto) -> requiere OTP manual" >> "$LOG"
    fi
  fi
done
