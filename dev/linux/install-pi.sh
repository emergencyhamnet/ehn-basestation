#!/usr/bin/env bash
set -euo pipefail
umask 077

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
[[ "$EUID" -ne 0 ]] || { printf 'Run this as the normal service user, not root.\n' >&2; exit 1; }
[[ "$#" -eq 3 ]] || { printf 'Usage: bash dev/linux/install-pi.sh MESHCORE_BY_ID MESHTASTIC_BY_ID ANDROID_SERIAL\n' >&2; exit 1; }
for device in "$1" "$2"; do
    [[ -c "$device" && -r "$device" && -w "$device" ]] || { printf 'Serial device is missing or inaccessible: %s\n' "$device" >&2; exit 1; }
done
[[ "$(readlink -f "$1")" != "$(readlink -f "$2")" ]] || { printf 'Select two different radio devices.\n' >&2; exit 1; }
for command in python3 adb systemctl systemd-analyze; do
    command -v "$command" >/dev/null || { printf 'Missing prerequisite: %s\n' "$command" >&2; exit 1; }
done
[[ -f "$root/ui/static/workbench-preview.html" ]] || { printf 'Source bundle must include ui/static/.\n' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
cd "$root"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install -e '.[meshtastic,meshcore-usb]'
.venv/bin/python dev/linux/configure_pi.py --meshcore-port "$1" --meshtastic-port "$2" --android-serial "$3"
.venv/bin/python -m pip freeze > var/pi/installed-packages.txt
systemctl --user daemon-reload
systemd-analyze --user verify "$HOME/.config/systemd/user/ehn-meshcore-usb.service" "$HOME/.config/systemd/user/ehn-basestation.service" "$HOME/.config/systemd/user/ehn-android-usb.service"
systemctl --user enable --now ehn-meshcore-usb.service ehn-basestation.service ehn-android-usb.service
printf '\nUser services enabled. UI: http://<pi-address>:18098\n'
printf 'Profile: %s/var/pi/station.env (private; do not share).\n' "$root"
printf 'Configure the phone token locally. Unattended boot also requires loginctl enable-linger for this user.\n'