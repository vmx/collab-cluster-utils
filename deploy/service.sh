#!/usr/bin/env bash
# Manages one of this repo's tools (publisher, data-manager or rescuer) as a
# persistent systemd --user unit that survives reboots/re-logins.
#
# The real unit file is generated from
# collab-cluster-<tool>.service.template and kept in this repo
# (deploy/collab-cluster-<tool>.service, gitignored -- it embeds this
# machine's absolute repo path). `systemctl --user link` only adds a
# symlink under ~/.config/systemd/user pointing back at it, so nothing
# but that symlink is written outside the project directory.
#
# To just run it in the current terminal session instead, use
# `uv run collab-cluster-<tool>` directly.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    echo "usage: $0 {publisher|data-manager|rescuer} {install|uninstall|start|stop|restart|status|logs}" >&2
    exit 1
}

case "${1:-}" in
    publisher|data-manager|rescuer) unit="collab-cluster-$1" ;;
    *) usage ;;
esac
template="$repo_dir/deploy/$unit.service.template"
unit_file="$repo_dir/deploy/$unit.service"

require_venv() {
    if [ ! -x "$repo_dir/.venv/bin/$unit" ]; then
        echo "error: $repo_dir/.venv not found -- run 'uv sync' first" >&2
        exit 1
    fi
}

check_linger() {
    if [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != "yes" ]; then
        cat >&2 <<EOF
warning: lingering is not enabled for $(id -un) -- this unit will stop
  when you log out, not just on reboot. Enable it with:
    sudo loginctl enable-linger $(id -un)
  (no reboot needed, takes effect immediately)
EOF
    fi
}

install_unit() {
    require_venv
    check_linger

    sed "s|__REPO_DIR__|$repo_dir|g" "$template" > "$unit_file"

    systemctl --user link "$unit_file"
    systemctl --user daemon-reload
    systemctl --user enable --now "$unit"

    echo "installed and started -- see: systemctl --user status $unit"
}

uninstall_unit() {
    # `disable` removes both the enablement symlink and the one `link`
    # created, leaving only the generated file in this repo.
    systemctl --user disable --now "$unit" 2>/dev/null || true
    systemctl --user daemon-reload
    rm -f "$unit_file"
    echo "uninstalled"
}

case "${2:-}" in
    install) install_unit ;;
    uninstall) uninstall_unit ;;
    start) systemctl --user start "$unit" ;;
    stop) systemctl --user stop "$unit" ;;
    restart) systemctl --user restart "$unit" ;;
    status) systemctl --user status "$unit" ;;
    logs) journalctl --user -u "$unit" -f ;;
    *) usage ;;
esac
