"""Entry point for hosting or joining the LAN Gin Rummy application."""

from __future__ import annotations

import argparse
import time

from ai_client import start_ai_thread
from client_ui import connect_client, launch_client_ui, make_client_context
from server import DEFAULT_PORT, DEFAULT_TURN_TIMEOUT_SECONDS, make_server_context, process_one_event, start_server, stop_server


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(description="Two-player LAN Gin Rummy")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--host", action="store_true", help="Host a new LAN match and join it locally.")
    mode.add_argument("--join", metavar="HOST", help="Join an existing LAN match by host/IP.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port. Default: {DEFAULT_PORT}")
    parser.add_argument("--name", default="Player", help="Display name shown in the game UI.")
    parser.add_argument("--password", default="", help="Optional shared password required to join the game.")
    parser.add_argument("--ai", action="store_true", help="Host a solo match against a built-in computer player.")
    parser.add_argument("--seed", type=int, default=None, help="Optional deterministic seed for testing the host deck order.")
    parser.add_argument(
        "--turn-timeout",
        type=int,
        default=DEFAULT_TURN_TIMEOUT_SECONDS,
        help="Optional seconds allowed per turn before the server auto-plays. Use 0 to disable.",
    )
    return parser


def run_host(
    port: int,
    name: str,
    seed: int | None,
    password: str = "",
    use_ai: bool = False,
    turn_timeout: int = DEFAULT_TURN_TIMEOUT_SECONDS,
) -> None:
    """Run host mode by starting the server and local client UI.

    Args:
        port: TCP port.
        name: Local player display name.
        seed: Optional deterministic deck seed.
    """
    server_context = make_server_context(port=port, seed=seed, password=password, turn_timeout_seconds=turn_timeout)
    start_server(server_context)
    time.sleep(0.1)

    client_context = make_client_context("127.0.0.1", port, name, password=password)
    connect_client(client_context)

    if use_ai:
        start_ai_thread("127.0.0.1", port, name="Computer", password=password)
        banner = f"Hosting locally versus Computer. Turn timeout: {turn_timeout or 'off'}."
    else:
        banner = (
            f"Hosting on {server_context['local_ip']}:{port}   "
            f"Share that address with the other player. Turn timeout: {turn_timeout or 'off'}."
        )

    def tick() -> None:
        """Pump one server event inside the tkinter loop."""
        process_one_event(server_context, timeout=0.001)

    try:
        launch_client_ui(client_context, host_banner=banner, tick_callback=tick)
    finally:
        stop_server(server_context)


def run_client(host: str, port: int, name: str, password: str = "") -> None:
    """Run join mode and open the GUI.

    Args:
        host: Remote server host or IP.
        port: TCP port.
        name: Player display name.
    """
    client_context = make_client_context(host, port, name, password=password)
    connect_client(client_context)
    banner = f"Connected to {host}:{port}"
    launch_client_ui(client_context, host_banner=banner)


def main() -> None:
    """Parse arguments and start the requested mode."""
    parser = build_parser()
    args = parser.parse_args()
    if args.host:
        run_host(
            port=args.port,
            name=args.name,
            seed=args.seed,
            password=args.password,
            use_ai=args.ai,
            turn_timeout=args.turn_timeout,
        )
    else:
        run_client(host=args.join, port=args.port, name=args.name, password=args.password)


if __name__ == "__main__":
    main()
