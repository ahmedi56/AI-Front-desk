#!/bin/bash
# FIRST-TIME SERVER SETUP for a fresh Ubuntu 24.04 VPS. Read it first. Run it as root ONLY after you understand it.
# What it does: updates the system, makes a normal user, turns off password logins and root login over SSH,
# opens only ports 22, 80, 443, blocks repeated login guessing, turns on automatic security updates, installs Docker.
# BEFORE you run it: copy your SSH PUBLIC key to /root/.ssh/authorized_keys and test that you can log in with it,
# and put the name you want in ADMIN_USER. If you lock yourself out, use the provider's web console to fix it.
set -euo pipefail
ADMIN_USER="${ADMIN_USER:-deploy}"
apt-get update && apt-get -y upgrade
apt-get -y install ufw fail2ban unattended-upgrades ca-certificates curl gnupg
id "$ADMIN_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN_USER"
mkdir -p "/home/$ADMIN_USER/.ssh" && cp /root/.ssh/authorized_keys "/home/$ADMIN_USER/.ssh/authorized_keys"
chown -R "$ADMIN_USER:$ADMIN_USER" "/home/$ADMIN_USER/.ssh" && chmod 700 "/home/$ADMIN_USER/.ssh" && chmod 600 "/home/$ADMIN_USER/.ssh/authorized_keys"
usermod -aG sudo "$ADMIN_USER"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<CONF
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
MaxAuthTries 3
CONF
systemctl restart ssh
ufw default deny incoming && ufw default allow outgoing
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
ufw --force enable
systemctl enable --now fail2ban
dpkg-reconfigure -f noninteractive unattended-upgrades
# Docker (official repository)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
usermod -aG docker "$ADMIN_USER"
echo "Done. Open a NEW terminal and test: ssh $ADMIN_USER@THIS-SERVER. Only then close the old one."
