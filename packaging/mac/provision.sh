#!/bin/bash
# Provisions the Kelo server VM (Lima, mode system, runs as root inside the VM at first boot and again after each bundle update).
# The same steps as packaging/wsl/Dockerfile.distro: Docker Engine from Docker's apt repository (key fingerprint checked), uv,
# the user kelo, and a unit that starts Kelo's compose project at boot. Idempotent: safe to run again.
# Bump PROVISION_VERSION whenever this script changes; the user stage compares it with /var/lib/kelo/provisioned.
set -eux

PROVISION_VERSION=1

# Pins: packaging/mac/vm.lock is the source. tools/build/mac-server-bundle.sh fills the two @...@ values in the bundle's copy.
UV_VERSION="@UV_VERSION@"
UV_AARCH64_SHA256="@UV_AARCH64_SHA256@"
DOCKER_GPG_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git gnupg procps unattended-upgrades

# Docker Engine + Compose plugin from Docker's official apt repository (same key check as the WSL distro)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
actual_fp="$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '$1=="fpr"{print $10; exit}')"
if [ "$actual_fp" != "$DOCKER_GPG_FINGERPRINT" ]; then
    echo "Docker GPG fingerprint mismatch: expected $DOCKER_GPG_FINGERPRINT but got $actual_fp" >&2
    exit 1
fi
arch="$(dpkg --print-architecture)"
echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker

# The user kelo (uid 1000, the same as the WSL distro) may run docker
if ! id -u kelo >/dev/null 2>&1; then
    useradd -m -u 1000 -s /bin/bash kelo
fi
usermod -aG docker kelo
# A session the user already has (Lima opens one at boot) keeps the groups it started with: end it, so the next login (limactl shell,
# the install) has the docker group. Found 2026-11-17: 'docker info' was denied right after provisioning. Detached and delayed: on an
# upgrade this script runs inside such a session (limactl shell as kelo, then sudo), and ending it at once would kill this script.
setsid sh -c 'sleep 5; loginctl terminate-user kelo' </dev/null >/dev/null 2>&1 &

# uv, checked against its pinned sha256 before it is installed
if [ ! -x /usr/local/bin/uv ] || [ "$(/usr/local/bin/uv --version | awk '{print $2}')" != "$UV_VERSION" ]; then
    d="$(mktemp -d)"
    curl -fsSL -o "$d/uv.tar.gz" "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-aarch64-unknown-linux-gnu.tar.gz"
    echo "${UV_AARCH64_SHA256}  $d/uv.tar.gz" | sha256sum -c -
    tar -xzf "$d/uv.tar.gz" -C "$d"
    find "$d" -type f -name uv -exec install -m 0755 {} /usr/local/bin/uv \;
    find "$d" -type f -name uvx -exec install -m 0755 {} /usr/local/bin/uvx \;
    rm -rf "$d"
fi

# Security updates from Debian's security archive only
cat > /etc/apt/apt.conf.d/52kelo-unattended-upgrades <<'CONF'
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
};
CONF

# Starts Kelo's compose project at boot. The containers have restart: unless-stopped as well.
cat > /etc/systemd/system/kelo-up.service <<'UNIT'
[Unit]
Description=Kelo server (docker compose up)
After=docker.service
Requires=docker.service
ConditionPathExists=/home/kelo/kelo/compose.yml

[Service]
Type=oneshot
RemainAfterExit=yes
User=kelo
WorkingDirectory=/home/kelo/kelo
ExecStart=/usr/bin/docker compose -p kelo up -d

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable kelo-up.service

install -d -o kelo -g kelo /opt/kelo-installer
install -d /var/lib/kelo
echo "$PROVISION_VERSION" > /var/lib/kelo/provisioned
