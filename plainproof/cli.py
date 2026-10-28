"""Command-line entry point for checks, offline replay, and the loopback web UI."""
import argparse
import asyncio
from .common import PRICES
from .runner import Config, run
from .replay import replay


def main():
    parser = argparse.ArgumentParser(description='Plainproof: proof before accepting an AI-built change')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check')
    source = check.add_mutually_exclusive_group(required=True)
    source.add_argument('--app-dir')
    source.add_argument('--app-url')
    check.add_argument('--requirement', required=True)
    check.add_argument('--model', choices=sorted(PRICES), default='muse-spark-1.3')
    check.add_argument('--approve-confirmations', action='store_true')
    check.add_argument('--mock', action='store_true')
    check.add_argument('--mock-scenario', choices=['normal', 'safety', 'confirm', 'never_reload', 'invalid_plan', 'noop'], default='normal')
    rep = sub.add_parser('replay')
    rep.add_argument('receipt_dir')
    source = rep.add_mutually_exclusive_group(required=True)
    source.add_argument('--app-dir')
    source.add_argument('--app-url')
    rep.add_argument('--mock', action='store_true', help='Accepted for convenience; replay always makes zero model calls')
    fix = sub.add_parser('repair')
    fix.add_argument('receipt_dir')
    fix.add_argument('--app-dir', required=True)
    fix.add_argument('--model', choices=sorted(PRICES), default='muse-spark-1.3')
    fix.add_argument('--fixer', choices=['spark', 'muse-code'], default='spark')
    fix.add_argument('--fresh-check', action='store_true')
    fix.add_argument('--approve-confirmations', action='store_true')
    fix.add_argument('--mock', action='store_true')
    for cmd in (check, rep, fix):
        cmd.add_argument('--out', required=True)
        cmd.add_argument('--max-steps', type=int, default=25)
        cmd.add_argument('--timeout-s', type=float, default=180)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--out', default='runs')
    serve.add_argument('--timeout-s', type=float, default=180, help='time budget per web run')
    args = parser.parse_args()
    values = vars(args).copy()
    command = values.pop('command')
    if command == 'serve':
        from web.server import serve
        serve(args.port, args.out, args.timeout_s)
        return
    receipt_dir = values.pop('receipt_dir', None)
    fixer, fresh_check = values.pop('fixer', 'spark'), values.pop('fresh_check', False)
    config = Config(**values)
    if config.max_steps < 1 or config.timeout_s <= 0:
        parser.error('Step and time budgets must be positive')
    if not config.mock and config.mock_scenario != 'normal':
        parser.error('--mock-scenario requires --mock')
    try:
        if command == 'repair':
            from .repair import repair
            task = repair(receipt_dir, config, fixer=fixer, fresh_check=fresh_check)
        else:
            task = run(config) if command == 'check' else replay(receipt_dir, config)
        result = asyncio.run(task)
    except Exception as exc:
        parser.exit(2, f'Run setup failed ({type(exc).__name__}); check arguments and use a fresh --out directory.\n')
    print(f"{result['verdict']} ({result.get('reason') or result.get('stop_reason', 'replay decides')}); artifacts: {args.out}")
    raise SystemExit({'PASS': 0, 'FAIL': 1, 'FIXED': 0, 'NOT_FIXED': 1}.get(result['verdict'], 2))
