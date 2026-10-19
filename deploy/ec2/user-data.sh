#!/bin/bash
# Open Slot on Amazon Linux 2023 (x86_64): the app on 127.0.0.1:8760, behind Caddy, which gets and
# renews a Let's Encrypt certificate for <public ip with dashes>.sslip.io, and serves the site over HTTPS.
#
# Paste the whole file into the launch wizard's Advanced details > User data. The one line you edit is
# the QLOO_API_KEY line below: type the key straight after the =, with no quotes and no spaces. Nothing
# else in this file is secret, and the committed file never holds a key. AWS runs user data once, as
# root, on the first boot, and keeps a copy of the script on the instance (docs/deploy.md).
set -euo pipefail

QLOO_API_KEY=            # <<< the one line to edit: the Qloo key goes here, after the =
PROVIDER=live            # live calls Qloo and needs the key; fake serves synthetic sample data (a rehearsal)
DAILY_LIVE_CALLS=600     # the server's daily ceiling of live Qloo calls; set it from the key's own quota
REPO_URL=https://github.com/guptachetan1995/open-slot
REF=main                 # a branch, a tag or a commit of that repository
DNS_NAME=sslip.io        # nip.io is the same service under its other name, if Let's Encrypt limits sslip.io
CADDY_VERSION=2.11.7
CADDY_SHA512=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7

say() {
  echo "open-slot: $*"
  { echo "open-slot: $*" > /dev/console; } 2> /dev/null || true
}

case "$PROVIDER" in
  live | fake) ;;
  *) say "PROVIDER must be live or fake; nothing was installed"; exit 1 ;;
esac
if [ "$PROVIDER" = live ] && [ -z "$QLOO_API_KEY" ]; then
  say "PROVIDER is live and the QLOO_API_KEY line is empty; nothing was installed. Launch again with the key typed in."
  exit 1
fi

say "installing Python, one of 3.13, 3.12 or 3.11 (the app needs 3.11 or newer)"
dnf install -y tar gzip
PYV=
for v in 3.13 3.12 3.11; do
  if dnf install -y "python$v"; then PYV=$v; break; fi
done
[ -n "$PYV" ] || { say "no python3.13, python3.12 or python3.11 package; nothing else was installed"; exit 1; }
say "$("/usr/bin/python$PYV" --version) at /usr/bin/python$PYV"

TOKEN=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
IP=
for _ in $(seq 1 12); do
  IP=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
  if [[ "$IP" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then break; fi
  IP=
  sleep 5
done
[ -n "$IP" ] || { say "the instance has no public IPv4 address; auto-assign public IP must be Enable"; exit 1; }
HOST="${IP//./-}.$DNS_NAME"
say "this instance is $IP, so the site is https://$HOST/"

mkdir -p /opt/open-slot
curl -fsSL "$REPO_URL/archive/$REF.tar.gz" | tar -xz -C /opt/open-slot --strip-components=1
useradd --system --no-create-home --shell /sbin/nologin openslot
mkdir -p /opt/open-slot/.cache
chown openslot:openslot /opt/open-slot/.cache
chmod 700 /opt/open-slot/.cache

( umask 077
  {
    printf 'QLOO_API_KEY=%s\n' "$QLOO_API_KEY"
    printf 'OPEN_SLOT_PROVIDER=%s\n' "$PROVIDER"
    printf 'OPEN_SLOT_ALLOWED_HOSTS=%s\n' "$HOST"
    printf 'OPEN_SLOT_TRUST_PROXY=1\n'
    printf 'OPEN_SLOT_DAILY_LIVE_CALLS=%s\n' "$DAILY_LIVE_CALLS"
  } > /etc/open-slot.env )

curl -fsSLo /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v$CADDY_VERSION/caddy_${CADDY_VERSION}_linux_amd64.tar.gz"
echo "$CADDY_SHA512  /tmp/caddy.tar.gz" | sha512sum -c -
tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy
chmod 755 /usr/local/bin/caddy
rm -f /tmp/caddy.tar.gz
mkdir -p /etc/caddy
printf '%s {\n    reverse_proxy 127.0.0.1:8760\n}\n' "$HOST" > /etc/caddy/Caddyfile
useradd --system --create-home --home-dir /var/lib/caddy --shell /sbin/nologin caddy

cat > /etc/systemd/system/open-slot.service <<UNIT
[Unit]
Description=Open Slot
Wants=network-online.target
After=network-online.target

[Service]
User=openslot
Group=openslot
WorkingDirectory=/opt/open-slot
EnvironmentFile=/etc/open-slot.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python$PYV -m open_slot serve
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy
Wants=network-online.target
After=network-online.target open-slot.service

[Service]
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now open-slot.service caddy.service

for _ in $(seq 1 30); do
  if [ "$(curl -fsS --max-time 3 http://127.0.0.1:8760/healthz || true)" = ok ]; then break; fi
  sleep 1
done
[ "$(curl -fsS --max-time 3 http://127.0.0.1:8760/healthz)" = ok ]
say "the app answers on 127.0.0.1:8760 ($PROVIDER), and the Caddy checksum matched"

SECONDS=0
until [ "$(curl -fsS --max-time 10 "https://$HOST/healthz" || true)" = ok ]; do
  if [ "$SECONDS" -ge 600 ]; then
    say "no certificate seen from this instance after 10 minutes; Caddy's own lines are above; the name is https://$HOST/"
    exit 1
  fi
  sleep 10
done
say "python$PYV, caddy $CADDY_VERSION (checksum ok), certificate ok after $SECONDS seconds: https://$HOST/"
