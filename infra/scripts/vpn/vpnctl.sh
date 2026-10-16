#!/bin/bash
# Gestión de túneles openfortivpn POR SISTEMA, para que el host (y por ende el
# contenedor abap-mcp) alcance el SAP de cada cliente. Corre como root en el
# host, invocado por SSM. La config (incl. password) vive en SSM SecureString
# en /fabrica-sw/vpn/<system> como JSON:
#   { "gateway":"vpn.cliente.com", "port":443, "username":"u", "password":"p",
#     "realm":"", "trusted_cert":"<sha256>", "routes":["10.1.2.0/24"], "otp":true }
# `routes` es OBLIGATORIO: solo esas subredes se rutean por el túnel (split).
# `otp` (opcional, default true): false = la VPN NO usa token (sube con
# usuario/clave); 'up' espera a que levante y NO pide código. true/ausente = OTP.
# NUNCA dejamos que el gateway empuje una ruta default (set-routes=0): este host
# corre otros servicios y un default por la VPN los tumbaría a todos.
#
# Uso: vpnctl.sh status|up|otp|down <system> [otp_code]
#   up   -> arranca openfortivpn; dispara el OTP por correo; queda esperando.
#   otp  -> entrega el código recibido; completa el login y agrega las rutas.
set -uo pipefail

PREFIX="${SSM_VPN_PREFIX:-/fabrica-sw/vpn}"
REGION="${AWS_REGION:-us-east-1}"
RUN=/run/vpn

cmd="${1:-}"; sys="${2:-}"; arg="${3:-}"
if [ -z "$cmd" ] || [ -z "$sys" ]; then
  echo "uso: vpnctl.sh status|up|otp|down|reroute <system> [otp]"; exit 2
fi
mkdir -p "$RUN"; chmod 700 "$RUN"

pidf="$RUN/$sys.pid"; fifo="$RUN/$sys.otp"; log="$RUN/$sys.log"; cfg="$RUN/$sys.conf"

_conf() { aws ssm get-parameter --name "$PREFIX/$sys" --with-decryption \
            --region "$REGION" --query Parameter.Value --output text 2>/dev/null; }
_jget() { python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }
_routes() { python3 -c "import sys,json;print('\n'.join(json.load(sys.stdin).get('routes',[]) or []))"; }

_pid() { [ -f "$pidf" ] && cat "$pidf" 2>/dev/null; }
_alive() { local p; p="$(_pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
_up() { _alive && grep -q "Tunnel is up and running" "$log" 2>/dev/null; }
_iface() { grep -oE 'ppp[0-9]+' "$log" 2>/dev/null | tail -1; }
_addroutes() {  # agrega las rutas del sistema a la iface del túnel
  local iface="$1" j; j="$(_conf)"
  printf '%s' "$j" | _routes | while read -r r; do
    [ -n "$r" ] && ip route replace "$r" dev "$iface" && echo "  ruta $r -> $iface"
  done
}

case "$cmd" in

status)
  if _up; then echo "UP system=$sys iface=$(_iface)"; exit 0
  elif _alive; then echo "CONNECTING system=$sys (esperando OTP?)"; exit 0
  else echo "DOWN system=$sys"; exit 0; fi
  ;;

up)
  if _up; then echo "ALREADY-UP iface=$(_iface)"; exit 0; fi
  _alive && { kill "$(_pid)" 2>/dev/null; sleep 1; }
  j="$(_conf)"; [ -z "$j" ] || [ "$j" = "None" ] && { echo "ERROR: no hay config en $PREFIX/$sys"; exit 1; }
  gw="$(printf '%s' "$j"|_jget gateway)"; port="$(printf '%s' "$j"|_jget port)"
  user="$(printf '%s' "$j"|_jget username)"; pass="$(printf '%s' "$j"|_jget password)"
  realm="$(printf '%s' "$j"|_jget realm)"; cert="$(printf '%s' "$j"|_jget trusted_cert)"
  otp="$(printf '%s' "$j"|_jget otp | tr 'A-Z' 'a-z')"
  if [ -z "$(printf '%s' "$j"|_routes)" ]; then
    echo "ERROR: 'routes' vacío en $PREFIX/$sys (obligatorio por seguridad)"; exit 1
  fi
  umask 077
  { echo "host = $gw"; echo "port = ${port:-443}";
    echo "username = $user"; echo "password = $pass";
    [ -n "$realm" ] && echo "realm = $realm";
    [ -n "$cert" ]  && echo "trusted-cert = $cert";
    echo "set-routes = 0"; echo "set-dns = 0"; echo "persistent = 0"; } > "$cfg"
  rm -f "$fifo"; mkfifo "$fifo"; chmod 600 "$fifo"
  # 0<>fifo: abre el fifo lectura/escritura -> openfortivpn arranca YA (sin
  # bloquear ni recibir EOF) y queda esperando el OTP cuando 'otp' lo escriba.
  setsid openfortivpn -c "$cfg" 0<>"$fifo" >"$log" 2>&1 &
  echo $! > "$pidf"
  # Sin trusted_cert la 1ª vez openfortivpn aborta pidiendo el digest: lo damos.
  if [ -z "$cert" ]; then
    for i in $(seq 1 10); do grep -q "trusted-cert" "$log" 2>/dev/null && break; _alive || break; sleep 1; done
    dg="$(grep -oE 'trusted-cert [0-9a-f]{64}' "$log" 2>/dev/null | tail -1 | awk '{print $2}')"
    if [ -n "$dg" ]; then
      echo "NEED-CERT digest=$dg"
      echo "  -> guarda trusted_cert=$dg en $PREFIX/$sys y reintenta 'up'"
      exit 3
    fi
  fi
  sleep 2
  if grep -qiE "authentication failed|denied|error" "$log" 2>/dev/null && ! _alive; then
    echo "ERROR al conectar:"; tail -5 "$log"; exit 1
  fi
  # VPN SIN OTP (otp=false/no/0/off en la config): el túnel sube solo con
  # usuario/clave; no hay que pedir código. Esperamos a que levante y rutamos.
  case "$otp" in
    false|no|0|off)
      for i in $(seq 1 20); do _up && break; _alive || break; sleep 1; done
      if _up; then
        iface="$(_iface)"; _addroutes "$iface"; echo "UP system=$sys iface=$iface"; exit 0
      fi
      echo "ERROR: VPN sin OTP pero el túnel no subió:"; tail -8 "$log"; exit 1
      ;;
  esac
  echo "OTP-REQUESTED system=$sys — revisa el correo y entrega el código con: otp $sys <code>"
  ;;

otp)
  [ -z "$arg" ] && { echo "uso: vpnctl.sh otp $sys <code>"; exit 2; }
  [ -p "$fifo" ] || { echo "ERROR: no hay conexión pendiente; corre 'up $sys' primero"; exit 1; }
  _alive || { echo "ERROR: el proceso openfortivpn ya no está; reintenta 'up'"; tail -5 "$log"; exit 1; }
  printf '%s\n' "$arg" > "$fifo"
  for i in $(seq 1 25); do _up && break; _alive || break; sleep 1; done
  if _up; then
    iface="$(_iface)"
    _addroutes "$iface"
    echo "UP system=$sys iface=$iface"
  else
    echo "FALLÓ tras el OTP:"; tail -8 "$log"; exit 1
  fi
  ;;

down)
  if _alive; then kill "$(_pid)" 2>/dev/null; sleep 1; _alive && kill -9 "$(_pid)" 2>/dev/null; fi
  rm -f "$pidf" "$fifo" "$cfg"
  echo "DOWN system=$sys"
  ;;

reroute)
  # Re-agrega SOLO las rutas del sistema a la interfaz ppp que hoy tiene el túnel
  # a este gateway. NO toca el proceso openfortivpn (no reinicia). Sirve para
  # cuando el túnel sigue vivo pero la ruta quedó stale (p.ej. ppp0/ppp1 se
  # intercambiaron al reconectar el otro cliente).
  j="$(_conf)"; { [ -z "$j" ] || [ "$j" = "None" ]; } && { echo "ERROR: no config en $PREFIX/$sys"; exit 1; }
  gw="$(printf '%s' "$j"|_jget gateway)"
  # buscar la iface cuyo peer sea el gateway del sistema (robusto ante swaps ppp)
  iface="$(ip -o addr show 2>/dev/null | awk -v g="peer $gw" 'index($0,g){print $2; exit}')"
  [ -z "$iface" ] && iface="$(_iface)"
  [ -z "$iface" ] && { echo "ERROR: sin iface para $sys (túnel caído?)"; exit 1; }
  printf '%s' "$j" | _routes | while read -r r; do
    [ -n "$r" ] && ip route replace "$r" dev "$iface" && echo "  ruta $r -> $iface"
  done
  echo "REROUTED system=$sys iface=$iface"
  ;;

*)
  echo "comando inválido: $cmd (status|up|otp|down|reroute)"; exit 2;;
esac
