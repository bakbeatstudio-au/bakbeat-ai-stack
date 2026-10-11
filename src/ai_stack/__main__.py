import argparse
import sys

from .config import DATABASE, load


def main():
    parser = argparse.ArgumentParser(description='BakBeat AI Stack foundation')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('serve', help='Start the local chat interface')
    sub.add_parser('smoke-test', help='Run the capped, idempotent two-provider connection test')
    sub.add_parser('check-providers', help='Authenticate and list models without generating text')
    sub.add_parser('doctor', help='Validate setup without accessing credentials')
    sub.add_parser('init-db', help='Initialize and check the private internal database')
    keys = sub.add_parser('setup-key', help='Enter one API key privately in Terminal')
    keys.add_argument('provider', choices=['openai', 'anthropic'])
    args = parser.parse_args()
    try:
        load()
        if args.command in ('serve', 'smoke-test'):
            from .runtime import run
            return run(args.command)
        elif args.command == 'check-providers':
            import json
            from .connectivity import check_provider
            results = [check_provider(p) for p in ('openai', 'anthropic')]
            for result in results:
                print(json.dumps(result))
            return 0 if all(r['status'] == 'authenticated' for r in results) else 1
        elif args.command == 'setup-key':
            from .credentials import provision
            provision(args.provider)
        else:
            from .storage import initialize, verify_internal_directory
            verify_internal_directory(DATABASE.parent)
            if args.command == 'init-db':
                initialize(DATABASE)
                print('Internal SQLite database ready; WAL enabled; integrity check passed.')
            else:
                print('Configuration valid: localhost only; future features disabled.')
                print('Database directory: private and verified on internal storage.')
                print('API credentials and provider connectivity: not checked.')
        return 0
    except (Exception, KeyboardInterrupt):
        # Exception bodies from credential libraries may contain sensitive data.
        print('Setup did not complete. Check the configuration, private directory, or login Keychain access. No diagnostic secret data is printed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
