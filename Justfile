image := "ghcr.io/tritacle/uct-room-booking:latest"

# Symlink the units into user scope
link:
    ln -sf "$(pwd)/systemd/uct-edge.service" ~/.config/systemd/user/uct-edge.service
    ln -sf "$(pwd)/systemd/uct-edge.timer" ~/.config/systemd/user/uct-edge.timer
    ln -sf "$(pwd)/systemd/uct-backfill.service" ~/.config/systemd/user/uct-backfill.service
    ln -sf "$(pwd)/systemd/uct-backfill.timer" ~/.config/systemd/user/uct-backfill.timer
    ln -sf "$(pwd)/systemd/uct-checkin.service" ~/.config/systemd/user/uct-checkin.service
    ln -sf "$(pwd)/systemd/uct-checkin.timer" ~/.config/systemd/user/uct-checkin.timer
    ln -sf "$(pwd)/systemd/uct-digest.service" ~/.config/systemd/user/uct-digest.service
    ln -sf "$(pwd)/systemd/uct-digest.timer" ~/.config/systemd/user/uct-digest.timer
    ln -sf "$(pwd)/systemd/uct-notify-failure.service" ~/.config/systemd/user/uct-notify-failure.service
    systemctl --user daemon-reload

start:
    systemctl --user enable --now uct-edge.timer uct-backfill.timer uct-checkin.timer uct-digest.timer

stop:
    systemctl --user disable --now uct-edge.timer uct-backfill.timer uct-checkin.timer uct-digest.timer

logs:
    journalctl --user -u uct-edge.service -f

next:
    systemctl --user list-timers 'uct-*'

once:
    systemctl --user start uct-edge.service

pull:
    docker pull {{image}}

# Local, no container
discover:
    uv run uct discover

status:
    uv run uct status

dry:
    uv run uct book

check:
    uv run ruff check . && uv run mypy && uv run pytest -q
