#!/bin/bash
# Instala openfortivpn (cliente CLI FortiGate SSL-VPN) en el host AL2023 desde
# fuente. AL2023 no trae openfortivpn ni el binario pppd (aunque el kernel SÍ
# tiene PPP: /dev/ppp + ppp_generic.ko), así que compilamos AMBOS: ppp (pppd) y
# openfortivpn. Idempotente: si ya están, no recompila. Correr como root vía SSM.
set -euxo pipefail

dnf install -y git gcc make automake autoconf libtool pkgconfig openssl-devel python3

PPPD_BIN=/usr/local/sbin/pppd

# --- pppd (proyecto ppp) -----------------------------------------------------
if ! command -v pppd >/dev/null 2>&1 && [ ! -x "$PPPD_BIN" ]; then
  tmp_ppp="$(mktemp -d)"
  git clone --depth 50 https://github.com/ppp-project/ppp.git "$tmp_ppp/ppp"
  cd "$tmp_ppp/ppp"
  tag="$(git describe --tags "$(git rev-list --tags --max-count=1)")"
  git checkout "$tag"
  [ -x ./autogen.sh ] && ./autogen.sh || true
  ./configure --prefix=/usr/local --sysconfdir=/etc
  make
  make install
  cd /
  rm -rf "$tmp_ppp"
  echo "pppd $tag -> $PPPD_BIN"
else
  echo "pppd ya presente: $(command -v pppd || echo "$PPPD_BIN")"
fi

# --- openfortivpn ------------------------------------------------------------
if ! command -v openfortivpn >/dev/null 2>&1; then
  tmp_ofv="$(mktemp -d)"
  git clone --depth 50 https://github.com/adrienverge/openfortivpn.git "$tmp_ofv/openfortivpn"
  cd "$tmp_ofv/openfortivpn"
  tag="$(git describe --tags "$(git rev-list --tags --max-count=1)")"
  git checkout "$tag"
  ./autogen.sh
  # Apuntar al pppd que acabamos de compilar.
  ./configure --prefix=/usr/local --sysconfdir=/etc --with-pppd="$(command -v pppd || echo "$PPPD_BIN")"
  make
  make install
  cd /
  rm -rf "$tmp_ofv"
  echo "openfortivpn $tag -> $(command -v openfortivpn)"
else
  echo "openfortivpn ya instalado: $(openfortivpn --version 2>&1 | head -1)"
fi

echo "OK install. pppd=$(command -v pppd || echo "$PPPD_BIN")  openfortivpn=$(command -v openfortivpn)"
