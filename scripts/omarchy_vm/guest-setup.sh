#!/usr/bin/env bash
set -euo pipefail

[[ $(id -u) == 0 ]]
[[ $(hostname) == omarchy-akshath ]]
[[ -f /usr/share/wayland-sessions/hyprland-uwsm.desktop ]]

sudoers=/etc/sudoers.d/90-akshath-provisioning
[[ ! -e $sudoers ]]
printf '%s\n' 'Defaults:akshath verifypw=never' 'akshath ALL=(ALL) NOPASSWD: ALL' > "$sudoers"
chmod 0440 "$sudoers"
trap 'rm -f "$sudoers"' EXIT
visudo -cf "$sudoers"
runuser -u akshath -- env HOME=/home/akshath bash -lc 'omarchy update -y'
rm -f "$sudoers"
trap - EXIT
pacman -S --noconfirm --needed prometheus-node-exporter qemu-guest-agent

cat > /etc/mkinitcpio.conf.d/zz-omarchy-vm.conf <<'EOF'
vm_hooks=()
for hook in "${HOOKS[@]}"; do
  case "$hook" in
    encrypt|resume|plymouth) ;;
    *) vm_hooks+=("$hook") ;;
  esac
done
HOOKS=("${vm_hooks[@]}")
unset vm_hooks hook
EOF
if ! grep -Fq 'plymouth.enable=0 noresume' /etc/default/limine; then
  printf '%s\n' 'KERNEL_CMDLINE[default]+=" plymouth.enable=0 noresume"' >> /etc/default/limine
fi
limine-mkinitcpio

session=hyprland-uwsm.desktop
[[ ! -f /usr/share/wayland-sessions/omarchy.desktop ]] || session=omarchy.desktop
printf '%s\n' '[Last]' "Session=$session" 'User=akshath' > /var/lib/sddm/state.conf
chown sddm:sddm /var/lib/sddm/state.conf

install -d -m 0755 /etc/ssh/sshd_config.d /etc/systemd/system/prometheus-node-exporter.service.d
cat > /etc/ssh/sshd_config.d/00-akshath.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t

cat > /etc/systemd/system/prometheus-node-exporter.service.d/omarchy-vm.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/prometheus-node-exporter --web.listen-address=10.250.60.2:9100 --collector.pressure
EOF
ufw allow from 10.250.60.1 to any port 9100 proto tcp
systemctl daemon-reload
systemctl enable --now prometheus-node-exporter.service qemu-guest-agent.service
loginctl enable-linger akshath
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl reload sshd.service

runuser -u akshath -- env HOME=/home/akshath bash -lc \
  'curl --fail --silent --show-error --location https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive'

# shellcheck disable=SC2016
runuser -u akshath -- env HOME=/home/akshath bash -lc '
  omarchy-install-hermes-cli --remove
  ln -sfn "$HOME/.hermes/hermes-agent/.hermes/bin/hermes" "$HOME/.local/bin/hermes"
  "$HOME/.local/bin/hermes" --version
'

env HERMES_HOME=/home/akshath/.hermes /home/akshath/.local/bin/hermes gateway install \
  --system --run-as-user akshath --no-start-now --no-start-on-login

systemctl is-active prometheus-node-exporter.service qemu-guest-agent.service sshd.service
