"""Record the retained FAIL UI and a separate receipt tab. Server runs separately."""
import argparse
from contextlib import contextmanager
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
    events = dict(version=2, mock=args.mock, base=base, phases=[], receipts={}, receipt_ids={}, commands=[], focus=[])
    clock = time.monotonic

    @contextmanager
    def phase(scene, name, waiting=False):
        item = dict(scene=scene, phase=name, waiting=waiting, video=page_name, start=clock())
        events['phases'].append(item)
        try:
            yield
        finally:
            item['end'] = clock()

    def receipt(run_id):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+(?:/replay)?', run_id):
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
        # Keep the UI alive: history navigation can discard failedRun in JS.
        # Each tab has an uncut video and its own measured recording clock.
        pages, videos, sync_clocks = {}, {}, {}
        for name in ('ui', 'receipt'):
            target = context.new_page()
            pages[name], videos[name] = target, target.video
            target.set_content('<body style="margin:0;background:rgb(19,37,71)"></body>')
            target.wait_for_timeout(400)
            before = clock()
            target.set_content('<body style="margin:0;background:rgb(36,66,99)"></body>')
            target.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
            sync_clocks[name] = (before + clock()) / 2
            target.wait_for_timeout(400)
        page_name, page = 'ui', pages['ui']

        def switch(name):
            nonlocal page_name, page
            page_name, page = name, pages[name]
            page.bring_to_front()

        def pause(seconds=3):
            page.wait_for_timeout(seconds * 1000)

        def go(run_id):
            response = page.goto(f'{base}/runs/{run_id}/receipt.html')
            if response.status != 200:
                raise RuntimeError('Receipt not served; use the updated web/server.py and matching --web-out')
            page.wait_for_function('[...document.images].every(i => i.complete && i.naturalWidth > 0)')

        def show(scene, name, locator, seconds=2.2):
            with phase(scene, name):
                locator.scroll_into_view_if_needed()
                locator.evaluate("el => el.scrollIntoView({block:'center'})")
                # Wait for the scroll paint before measuring viewport coordinates.
                page.evaluate('() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')
                box = locator.bounding_box()
                if not box:
                    raise RuntimeError('Focus target is not visible: ' + name)
                left, top = max(0, box['x']-24), max(0, box['y']-24)
                right = min(1280, box['x']+box['width']+24)
                bottom = min(900, box['y']+box['height']+24)
                focus = dict(scene=scene, phase=name, video=page_name, start=clock(),
                    rect=dict(x=left, y=top, width=right-left, height=bottom-top))
                events['focus'].append(focus)
                pause(seconds)
                focus['end'] = clock()

        def banner(scene, name, run_id):
            switch('receipt')
            with phase(scene, 'open_receipt'):
                go(run_id)
            show(scene, name, page.locator('#verdict'))

        def check_row(text, source=None):
            # Check IDs belong to the planner. Locate the literal expectation,
            # then the checkpoint source, rather than a mock's chosen ID.
            rows = page.locator('#checks tbody tr').filter(
                has=page.locator('td:nth-child(2)').filter(has_text=text))
            if source:
                rows = rows.and_(page.locator(f'[data-source="{source}"]'))
            return rows.first

        def enter(scene, variant, requirement):
            switch('ui')
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
            banner('receipt_fail', 'verdict', failed_id)
            show('receipt_fail', 'plan', page.locator('#plan'))
            show('receipt_fail', 'evidence', check_row('0 items left').filter(has_text='1 items left'))
            show('receipt_fail', 'audit', check_row('0 items left', 'audit'))
            switch('ui')
            with phase('repair', 'propose'):
                # This is still the exact failed run, without a reload or Run click.
                if page.locator('#receipt').get_attribute('href') != f'/runs/{failed_id}/receipt.html':
                    raise RuntimeError('Original FAIL UI state was lost; repair cannot start a new run')
                if page.locator('#status').inner_text() != 'FAIL':
                    raise RuntimeError('Original UI no longer holds the FAIL verdict')
                page.locator('#repair').scroll_into_view_if_needed()
                pause(1)
                page.locator('#repair').click()
            with phase('repair', 'waiting', True):
                page.locator('#repair-result').wait_for(state='visible', timeout=180000)
                page.locator('#repair-banner').filter(has_text=re.compile(
                    r'^(FIXED|NOT_FIXED|INCONCLUSIVE|REPAIR_REJECTED|ERROR)$')).wait_for(timeout=10000)
            repair_href = page.locator('#receipt').get_attribute('href')
            match = re.fullmatch(r'/runs/([a-zA-Z0-9_-]+)/repair.html', repair_href or '')
            if not match:
                raise RuntimeError('Repair result has no server-written repair receipt link')
            repair_id = match[1]
            events['receipt_ids']['repair'] = repair_id
            repaired = json.loads((args.web_out / repair_id / 'repair.json').read_text())
            events['repair'] = {k: repaired.get(k) for k in (
                'verdict', 'replay_verdict', 'model_calls', 'usd', 'seconds', 'mock', 'source_sha256')}
            if Path(repaired['source_receipt']).resolve() != args.web_out / failed_id / 'receipt.json':
                raise RuntimeError('Repair used a different FAIL receipt')
            if repaired['verdict'] != 'FIXED' or page.locator('#repair-banner').inner_text() != 'FIXED':
                raise RuntimeError('Repair is not FIXED: ' + str(repaired['verdict']))
            replay_id = repair_id + '/replay'
            events['receipt_ids']['repair_replay'] = replay_id
            replayed = receipt(replay_id)
            if (replayed['verdict'] != 'PASS' or
                    replayed['model_calls'] != {'planner': 0, 'operator': 0} or replayed['usd'] != 0):
                raise RuntimeError('Repair replay must be PASS with zero model calls and USD 0')
            show('repair', 'diagnosis', page.locator('#repair-diagnosis'), seconds=7)
            if not page.locator('#repair-diff .added').count() or not page.locator('#repair-diff .removed').count():
                raise RuntimeError('Repair diff lacks coloured additions/removals')
            show('repair', 'diff', page.locator('#repair-diff'), seconds=6)
            show('repair', 'fixed_banner', page.locator('#repair-banner'), seconds=3)
            with phase('repair', 'open_replay'):
                href = page.locator('#repair-replay').get_attribute('href')
                if href != f'/runs/{replay_id}/receipt.html':
                    raise RuntimeError('Repair replay link does not match the server receipt')
                # Repair has finished, so this UI tab can now follow its link.
                page.locator('#repair-replay').click()
            page.wait_for_url(f'{base}{href}')
            page.wait_for_function('[...document.images].every(i => i.complete && i.naturalWidth > 0)')
            show('repair', 'replay_verdict', page.locator('#verdict'), seconds=3)
            show('repair', 'run_details', page.locator('#run-details'), seconds=3)
            show('repair', 'zero_model_calls', page.locator('#model-calls'))
            show('repair', 'zero_usd', page.locator('#usd'))
            live_id, live = enter('live_new', 'correct', DELETE)
            events['receipt_ids']['live_new'] = live_id
            if live['verdict'] != 'PASS' or not any(s.get('marker_seen') for s in live['steps']) or not all(c['passed'] for c in live['check_results']):
                raise RuntimeError('New requirement lacks PASS / positive marker sighting / passing checks')
            banner('live_new', 'receipt_verdict', live_id)
            show('live_new', 'marker_seen', page.locator('#steps [data-step][data-marker-seen="true"]').first)
            show('live_new', 'final_checks', page.locator('#checks'))
            show('live_new', 'final_counter', check_row('0 items left', 'audit'))
            with phase('guard_control', 'cli_execution', True):
                page.set_content('<body style="background:#101c2c;color:white;padding:70px;font:28px system-ui"><h1>Offline negative control</h1><p>Running the mock CLI with an operator that does nothing…</p><p>Waiting for the actual receipt.</p></body>')
                control_cmd = command([sys.executable, '-m', 'plainproof', 'check', '--app-dir', str(args.repo / 'demo_app/correct'),
                    '--requirement', DELETE, '--mock', '--mock-scenario', 'noop', '--out', str(args.web_out / 'noop-control')])
            control = receipt('noop-control')
            events['receipt_ids']['guard_control'] = 'noop-control'
            if control_cmd['exit_code'] != 2 or control['verdict'] != 'INCONCLUSIVE' or control['verdict_reason'] != 'marker_never_seen' or not all(c['passed'] for c in control['check_results']):
                raise RuntimeError('No-op control does not satisfy expected marker guard evidence')
            banner('guard_control', 'verdict_reason', 'noop-control')
            show('guard_control', 'passing_checks', page.locator('#checks'))
            show('guard_control', 'passing_counter', check_row('0 items left', 'audit'))
        except Exception as exc:
            failure = exc
            events['error'] = str(exc)
        finally:
            context.close()
            raw_paths = {name: Path(video.path()).resolve() for name, video in videos.items()}
            browser.close()
    # Save even a rejected/partial take; clock-calibration errors must not lose events.
    events['raw_videos'] = {name: dict(path=str(path)) for name, path in raw_paths.items()}
    write(args.out / 'events.json', events)
    try:
        calibrations = {name: calibrate(path) for name, path in raw_paths.items()}
    except Exception as exc:
        events['error'] = events.get('error', 'Video clock calibration failed: ' + str(exc))
        write(args.out / 'events.json', events)
        raise SystemExit(events['error'])
    origins = {name: sync_clocks[name] - calibrations[name] for name in raw_paths}
    origin = origins['ui']
    for item in events['phases'] + events['focus']:
        item['start'] = round(item['start'] - origin, 4)
        item['end'] = round(item['end'] - origin, 4)
    events['raw_videos'] = {name: dict(path=str(path), offset_seconds=origins[name]-origin)
                            for name, path in raw_paths.items()}
    events['scenes'] = [dict(scene=scene, start=min(p['start'] for p in events['phases'] if p['scene']==scene),
        end=max(p['end'] for p in events['phases'] if p['scene']==scene))
        for scene in dict.fromkeys(p['scene'] for p in events['phases'])]
    events['clock_calibration'] = dict(method='encoded two-swatch sync per tab',
        sync_video_seconds=calibrations, tolerance_seconds=0.12)
    write(args.out / 'events.json', events)
    print(args.out / 'events.json')
    if failure:
        raise SystemExit(str(failure))


if __name__ == '__main__':
    main()
