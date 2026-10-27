"""Record a single browser page and actual CLI output. Server runs separately."""
import argparse
from contextlib import contextmanager
import html
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from common import HERE, write

COUNTER = 'After I add one to-do and mark it done, the counter decreases to 0 items left.'
DELETE = 'After I add one to-do and then delete it, it is gone from the list and the counter shows 0 items left.'


def calibrate(video):
    """Locate the second full-frame sync swatch in encoded frames (25 fps).

    This measures the recording origin rather than assuming new_page latency is
    zero. Only the first two seconds are decoded; no ffmpeg filters are used.
    """
    size = 1280 * 900 * 3
    proc = subprocess.Popen(['ffmpeg', '-v', 'error', '-i', str(video), '-frames:v', '50',
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    found = None
    try:
        for i in range(50):
            frame = proc.stdout.read(size)
            if len(frame) < size:
                break
            pixels = [frame[j:j+3] for j in (0, (450*1280+640)*3, size-3)]
            if all(max(abs(a-b) for a,b in zip(pixel, (36, 66, 99))) < 12 for pixel in pixels):
                found = i / 25
                break
    finally:
        proc.stdout.close()
        proc.wait()
    if found is None:
        raise RuntimeError('Could not calibrate video clock; retain raw video for inspection')
    return found


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', required=True)
    p.add_argument('--web-out', type=Path, required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--mock', action='store_true')
    p.add_argument('--out', type=Path, default=HERE / 'recordings')
    args = p.parse_args()
    base = args.base.rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        p.error('--base must be a loopback HTTP origin without credentials or path')
    args.repo, args.web_out, args.out = (x.resolve() for x in (args.repo, args.web_out, args.out))
    if args.out.exists() and any(args.out.iterdir()):
        p.error('--out must be fresh (raw videos are never overwritten)')
    args.out.mkdir(parents=True, exist_ok=True)
    events = dict(version=1, mock=args.mock, base=base, phases=[], receipts={}, receipt_ids={}, commands=[])
    clock = time.monotonic

    @contextmanager
    def phase(scene, name, waiting=False):
        item = dict(scene=scene, phase=name, waiting=waiting, start=clock())
        events['phases'].append(item)
        try:
            yield
        finally:
            item['end'] = clock()

    def receipt(run_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', run_id):
            raise RuntimeError('Unexpected receipt id')
        data = json.loads((args.web_out / run_id / 'receipt.json').read_text())
        events['receipts'][run_id] = {k: data.get(k) for k in
            ('verdict', 'verdict_reason', 'seconds', 'usd', 'model_calls', 'mock', 'original_verdict')}
        return data

    def command(argv):
        # Inherit the credential-free recording terminal. Never inspect its
        # environment, import the server, or load any environment file.
        result = subprocess.run(argv, cwd=args.repo, capture_output=True, text=True, timeout=420)
        entry = dict(command=shlex.join(argv), stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
        events['commands'].append(entry)
        return entry

    failure = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 900},
            record_video_dir=str(args.out), record_video_size={'width': 1280, 'height': 900}, reduced_motion='reduce')
        creation_started = clock()
        page = context.new_page()
        creation_finished = clock()
        video = page.video
        page.set_content('<body style="margin:0;background:rgb(19,37,71)"></body>')
        page.wait_for_timeout(400)
        before = clock()
        page.set_content('<body style="margin:0;background:rgb(36,66,99)"></body>')
        page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
        sync_clock = (before + clock()) / 2
        page.wait_for_timeout(400)

        def pause(seconds=3):
            page.wait_for_timeout(seconds * 1000)

        def go(run_id):
            response = page.goto(f'{base}/runs/{run_id}/receipt.html')
            if response.status != 200:
                raise RuntimeError('Receipt not served; use the updated web/server.py and matching --web-out')
            page.wait_for_function('[...document.images].every(i => i.complete && i.naturalWidth > 0)')

        def show(scene, name, locator, seconds=3):
            with phase(scene, name):
                locator.evaluate("el => { el.scrollIntoView({block:'start'}); window.scrollBy(0,-95); }")
                pause(seconds)

        def show_fragment(scene, name, locator, text):
            with phase(scene, name):
                locator.evaluate('''(el, text) => {
                    const start = el.textContent.indexOf(text);
                    if (start < 0) throw Error('Missing receipt evidence');
                    const range = document.createRange();
                    range.setStart(el.firstChild, start);
                    range.setEnd(el.firstChild, start + text.length);
                    window.scrollBy(0, range.getBoundingClientRect().top - 180);
                }''', text)
                pause(3)

        def enter(scene, variant, requirement):
            with phase(scene, 'typing'):
                page.goto(base)
                page.locator('#variant').select_option(variant)
                page.locator('#requirement').fill('')
                page.locator('#requirement').press_sequentially(requirement, delay=35)
                page.locator('#model').select_option('muse-spark-1.3')
                page.locator('#mock').set_checked(args.mock)
                page.locator('#approve').check()
                pause(1)
                page.locator('#run').click()
            with phase(scene, 'waiting', True):
                page.locator('#receipt[href]').wait_for(state='visible', timeout=420000)
            with phase(scene, 'result'):
                pause(3)
                href = page.locator('#receipt').get_attribute('href')
                run_id = href.split('/')[2]
            return run_id, receipt(run_id)

        try:
            with phase('intro', 'ui'):
                page.goto(base)
                pause(4)
            failed_id, failed = enter('run_fail', 'bug_counter', COUNTER)
            events['receipt_ids']['run_fail'] = failed_id
            if failed['verdict'] != 'FAIL' or '1 items left' not in json.dumps(failed['check_results']):
                raise RuntimeError('Counter run lacks expected FAIL / 1 items left; keep footage and inspect plan')
            with phase('receipt_fail', 'verdict'):
                go(failed_id)
                pause(3)
            show('receipt_fail', 'plan', page.get_by_role('heading', name='Plan', exact=True))
            show('receipt_fail', 'evidence', page.get_by_role('heading', name='Checks and evidence'))
            show('receipt_fail', 'audit', page.locator('h3').filter(has_text='audit').last)
            with phase('replay', 'select_correct'):
                page.goto(base)
                page.locator('#variant').select_option('correct')
                pause(1)
            replay_id = 'replay-' + failed_id
            events['receipt_ids']['replay'] = replay_id
            with phase('replay', 'cli_execution', True):
                replay_cmd = command([sys.executable, '-m', 'plainproof', 'replay', str(args.web_out / failed_id),
                    '--app-dir', str(args.repo / 'demo_app/correct'), '--out', str(args.web_out / replay_id)])
            with phase('replay', 'terminal'):
                terminal = '$ ' + replay_cmd['command'] + '\n\n' + replay_cmd['stdout'] + replay_cmd['stderr'] + f'\nExit code: {replay_cmd["exit_code"]}'
                # On-screen text only: show repo-relative paths so no local user/folder names are filmed.
                for long, short in ((sys.executable, 'python'), (str(args.repo.resolve()) + '/', ''),
                                    (str(args.repo) + '/', ''), (str(Path.home()), '~')):
                    terminal = terminal.replace(long, short)
                page.set_content('<html><body style="margin:0;background:#0d1726;color:#d9fce8;font:22px monospace;padding:64px"><h2>Plainproof / CLI replay</h2><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + html.escape(terminal) + '</pre></body></html>')
                pause(6)
            replayed = receipt(replay_id)
            if replay_cmd['exit_code'] != 0 or replayed['verdict'] != 'PASS' or replayed['model_calls'] != {'planner': 0, 'operator': 0} or replayed['usd'] != 0:
                raise RuntimeError('Replay did not produce PASS / zero model calls / USD 0')
            with phase('replay_receipt', 'verdict'):
                go(replay_id)
                pause(3)
            show('replay_receipt', 'run_details', page.get_by_role('heading', name='Run details'))
            details = page.locator('body > pre').last
            show_fragment('replay_receipt', 'zero_model_calls', details, '"model_calls"')
            show_fragment('replay_receipt', 'zero_usd', details, '"usd"')
            show('replay_receipt', 'counter_evidence', page.get_by_role('heading', name='Checks and evidence'))
            live_id, live = enter('live_new', 'correct', DELETE)
            events['receipt_ids']['live_new'] = live_id
            if live['verdict'] != 'PASS' or not any(s.get('marker_seen') for s in live['steps']) or not all(c['passed'] for c in live['check_results']):
                raise RuntimeError('New requirement lacks PASS / positive marker sighting / passing checks')
            with phase('live_new', 'receipt_verdict'):
                go(live_id)
                pause(3)
            show_fragment('live_new', 'marker_seen', page.locator('article pre').filter(has_text='"marker_seen": true').first, '"marker_seen": true')
            show('live_new', 'final_checks', page.get_by_role('heading', name='Checks and evidence'))
            # Real planners choose their own check ids, so find the final counter check by its text.
            show('live_new', 'final_counter', page.locator('article pre').filter(has_text='0 items left').first)
            with phase('guard_control', 'cli_execution', True):
                page.set_content('<body style="background:#101c2c;color:white;padding:70px;font:28px system-ui"><h1>Offline negative control</h1><p>Running the mock CLI with an operator that does nothing…</p><p>Waiting for the actual receipt.</p></body>')
                control_cmd = command([sys.executable, '-m', 'plainproof', 'check', '--app-dir', str(args.repo / 'demo_app/correct'),
                    '--requirement', DELETE, '--mock', '--mock-scenario', 'noop', '--out', str(args.web_out / 'noop-control')])
            control = receipt('noop-control')
            events['receipt_ids']['guard_control'] = 'noop-control'
            if control_cmd['exit_code'] != 2 or control['verdict'] != 'INCONCLUSIVE' or control['verdict_reason'] != 'marker_never_seen' or not all(c['passed'] for c in control['check_results']):
                raise RuntimeError('No-op control does not satisfy expected marker guard evidence')
            with phase('guard_control', 'verdict_reason'):
                go('noop-control')
                pause(3)
            show('guard_control', 'passing_checks', page.get_by_role('heading', name='Checks and evidence'))
            show('guard_control', 'passing_counter', page.locator('article pre').filter(has_text='"id": "counter"').first)
            with phase('closing', 'repository'):
                excerpt = '\n'.join(line for line in (args.repo / 'README.md').read_text().splitlines() if any(w in line.lower() for w in ('hackathon', 'october', 'oct 27', 'day-one')))
                snapshot = '\n'.join(str(x.relative_to(args.repo)) for x in sorted((args.repo / 'day1_minimal').glob('*')) if x.is_file())
                page.set_content('<body style="background:#101c2c;color:white;padding:70px;font:24px system-ui"><h1>Plainproof / implemented repository</h1><pre style="white-space:pre-wrap">' + html.escape(excerpt + '\n\n' + snapshot) + '</pre></body>')
                pause(4)
            with phase('closing', 'failing_receipt'):
                go(failed_id)
                pause(3)
        except Exception as exc:
            failure = exc
            events['error'] = str(exc)
        finally:
            context.close()
            raw_path = Path(video.path()).resolve()
            browser.close()
    sync_video = calibrate(raw_path)
    origin = sync_clock - sync_video
    for item in events['phases']:
        item['start'] = round(item['start'] - origin, 4)
        item['end'] = round(item['end'] - origin, 4)
    events['scenes'] = [dict(scene=scene, start=min(p['start'] for p in events['phases'] if p['scene']==scene),
        end=max(p['end'] for p in events['phases'] if p['scene']==scene))
        for scene in dict.fromkeys(p['scene'] for p in events['phases'])]
    events.update(raw_video=str(raw_path), clock_calibration=dict(method='encoded two-swatch sync',
        sync_video_seconds=sync_video, page_creation_seconds=creation_finished-creation_started, tolerance_seconds=0.12))
    write(args.out / 'events.json', events)
    print(args.out / 'events.json')
    if failure:
        raise SystemExit(str(failure))


if __name__ == '__main__':
    main()
