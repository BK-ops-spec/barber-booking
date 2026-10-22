from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from barber import certs, config
from barber.server import main as server_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Barber shop booking server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 run.py\n"
            "  python3 run.py 0.0.0.0 5000\n"
            "  python3 run.py --tls --cert certs/booking.crt --key certs/booking.key\n"
            "  python3 run.py --make-cert booking.example.com\n"
        ),
    )
    parser.add_argument("host", nargs="?", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("port", nargs="?", type=int, default=None, help="port (default 8000)")
    parser.add_argument("--tls", action="store_true", help="serve over HTTPS")
    parser.add_argument("--cert", default=None, help="PEM certificate file")
    parser.add_argument("--key", default=None, help="PEM private key file")
    parser.add_argument(
        "--redirect-http",
        type=int,
        default=0,
        metavar="PORT",
        help="also run a plain HTTP server on PORT that redirects to HTTPS",
    )
    parser.add_argument(
        "--make-cert",
        metavar="HOSTNAME",
        default=None,
        help="generate a self-signed certificate for HOSTNAME and exit",
    )
    parser.add_argument(
        "--cert-dir",
        default=str(config.BASE_DIR / "certs"),
        help="where to write generated certificates (default ./certs)",
    )
    parser.add_argument(
        "--cert-days", type=int, default=825, help="self-signed cert lifetime in days"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    if args.make_cert:
        cert_dir = Path(args.cert_dir)
        slug = args.make_cert.replace(":", "-").replace("/", "-")
        cert_path = cert_dir / f"{slug}.crt"
        key_path = cert_dir / f"{slug}.key"
        hosts = [args.make_cert]
        if args.make_cert in {"localhost", "127.0.0.1", "::1"}:
            hosts = ["localhost", "127.0.0.1", "::1"]
        elif "." not in args.make_cert:
            hosts.append("localhost")
        certs.generate_self_signed(
            cert_path, key_path, hostnames=hosts, days=args.cert_days
        )
        print(f"Certificate written to {cert_path}")
        print(f"Private key written to {key_path} (mode 0600)")
        print()
        print(certs.describe(cert_path))
        print()
        print("This is a self-signed certificate: browsers will warn until you trust it.")
        print("For a public deployment use a real CA instead:")
        print("  - Caddy with a real domain (automatic Let's Encrypt, see deploy/Caddyfile)")
        print("  - certbot / your hosting provider's TLS panel")
        return 0

    if args.tls:
        if not args.cert or not args.key:
            print("error: --tls requires --cert and --key", file=sys.stderr)
            print(
                "       run 'python3 run.py --make-cert your.domain.com' to create a"
                " self-signed pair first",
                file=sys.stderr,
            )
            return 2

    positional = [a for a in (args.host, args.port) if a is not None]
    return server_main(
        positional,
        tls_cert=args.cert if args.tls else None,
        tls_key=args.key if args.tls else None,
        redirect_port=args.redirect_http,
    )


if __name__ == "__main__":
    raise SystemExit(main())
