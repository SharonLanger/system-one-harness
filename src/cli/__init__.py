"""Command-line adapters; hook execution belongs to the existing gateway."""
import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Dispatch before argparse so gateway flags, validation, streams and exit
    # conventions remain owned entirely by the gateway.
    if argv and argv[0] == 'hook':
        from hooks.gateway import main as gateway_main
        return gateway_main(argv[1:])

    parser = argparse.ArgumentParser(prog='system-one-harness')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('hook', help='pass all following arguments to the hook gateway')
    commands.add_parser('doctor', help='report local CLI setup (no model checks)')
    logs = commands.add_parser('logs', help='print recent lines from the existing JSONL log')
    logs.add_argument('--last', type=int, default=None, metavar='N', help='number of lines (default: 5)')
    logs.add_argument('--ui', action='store_true', help='open the local read-only log viewer')
    logs.add_argument('--no-open', action='store_true', help='print viewer URL without opening a browser')
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        from cli.doctor import run
        return run()
    if args.ui:
        if args.last is not None:
            parser.error('--ui cannot be combined with --last')
        from log_viewer.cli import run as run_ui
        return run_ui(no_open=args.no_open)
    if args.no_open:
        parser.error('--no-open requires --ui')
    if args.last is None:
        args.last = 5
    if args.last <= 0:
        parser.error('--last must be a positive integer')
    from cli.logs import run
    return run(args.last)
