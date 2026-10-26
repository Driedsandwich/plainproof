"""Offline browser integration and protocol tests. No test framework or Meta calls."""
import asyncio
import json
import os
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from playwright.async_api import async_playwright
from plainproof.agent import Driver, sandbox
from plainproof.common import app_server
from plainproof.judge import evaluate
from plainproof.mocks import MockOperator, mock_plan
from plainproof.planner import validate_plan
from plainproof.replay import replay
from plainproof.runner import Config, run
from web.server import make_server

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'test-artifacts'
RELOAD = 'A new to-do is still in the list after I reload the page.'
COUNTER = 'After adding one to-do and marking it done, the counter decreases to 0 items left.'
DELETE = 'After I add one to-do and then delete it, it is gone from the list and the counter shows 0 items left.'
SENTINEL = 'test-credential-sentinel-never-write-6192'


def config(name, variant='correct', requirement=RELOAD, **kwargs):
    return Config(out=str(OUT / name), app_dir=str(ROOT / 'demo_app' / variant),
                  requirement=requirement, mock=True, **kwargs)


class NoOpOperator:
    """Observe the untouched page, then claim completion without changing it."""
    def __init__(self, page, marker):
        self.turn = 0

    async def create(self, **request):
        self.turn += 1
        output = ([dict(type='computer_call', call_id='idle', actions=[dict(type='wait')],
                        pending_safety_checks=[])] if self.turn == 1 else
                  [dict(type='message', content=[dict(type='output_text', text='The counter is correct.')])])
        return dict(id=f'idle-{self.turn}', output=output, usage={})


async def suite():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    # If any mock path accidentally instantiates a real client, fail immediately.
    with patch('openai.AsyncOpenAI', side_effect=AssertionError('Real model calls forbidden')):
        for variant, expected in [('bug_reload', 'FAIL'), ('correct', 'PASS')]:
            r = await run(config('reload-' + variant, variant))
            assert r['verdict'] == expected, r['errors']
            assert any(c['source'] == 'audit' for c in r['checkpoints'])
            if expected == 'FAIL':
                assert r['agrees'] is False  # mock deliberately claims success
            print(f'PASS reload / {variant} => {expected}')
        for variant, expected in [('bug_counter', 'FAIL'), ('correct', 'PASS')]:
            r = await run(config('counter-' + variant, variant, COUNTER))
            assert r['verdict'] == expected, r['errors']
            print(f'PASS counter / {variant} => {expected}')
        r = await replay(OUT / 'reload-bug_reload', config('replay-correct'))
        assert r['verdict'] == 'PASS', r
        assert r['model_calls'] == {'planner': 0, 'operator': 0} and r['usd'] == 0
        assert r['original_verdict'] == 'FAIL' and r['original_sha256']
        print('PASS replay bug receipt / correct => PASS; zero model calls, USD 0')
        r = await run(config('safety', mock_scenario='safety', approve_confirmations=True))
        assert r['verdict'] == 'stopped_safety_check'
        assert not r['steps'][0]['executed'] and r['acknowledged_confirmations'] == 0
        print('PASS non-confirmation safety check => stopped_safety_check; no actions')
        for approved, expected in [(False, 'stopped_safety_check'), (True, 'PASS')]:
            r = await run(config('confirm-' + str(approved), mock_scenario='confirm', approve_confirmations=approved))
            assert r['verdict'] == expected, r
            assert r['acknowledged_confirmations'] == (2 if approved else 0)
            print(f'PASS confirmation approval={approved} => {expected}')
        r = await run(config('invalid', mock_scenario='invalid_plan'))
        assert r['verdict'] == 'ERROR' and r['model_calls']['operator'] == 0
        print('PASS invalid planner JSON => ERROR; no operator calls')
        r = await run(config('no-reload', mock_scenario='never_reload'))
        assert r['verdict'] == 'INCONCLUSIVE'
        print('PASS missing model reload => INCONCLUSIVE')
        r = await run(config('budget', max_steps=1))
        assert r['verdict'] == 'INCONCLUSIVE' and r['stop_reason'] == 'max_steps'
        r = await run(config('timeout', timeout_s=.001))
        assert r['verdict'] == 'INCONCLUSIVE' and r['stop_reason'] == 'timeout'
        print('PASS step and time budgets => INCONCLUSIVE')
        for key in ('CTRL+R', 'CMD+R'):
            def factory(page, marker):
                return MockOperator(page, marker, 'reload', reload_key=key)
            r = await run(config('keys-' + key.replace('+', '-')), operator_factory=factory)
            assert r['verdict'] == 'PASS', r['errors']
        print('PASS Ctrl+R and Cmd+R => reload checkpoints')
        r = await run(config('delete', requirement='Delete a new to-do and ensure it stays removed after reload.'))
        assert r['verdict'] == 'PASS'
        print('PASS add / delete / reload => PASS')
        r = await run(config('delete-noop', requirement=DELETE), operator_factory=NoOpOperator)
        assert r['verdict'] == 'INCONCLUSIVE' and r['verdict_reason'] == 'marker_never_seen', r
        assert all(c['passed'] is True for c in r['check_results'])
        assert r['steps'][0]['marker_seen'] is False
        assert 'marker_never_seen' in (OUT / 'delete-noop/receipt.html').read_text()
        print('PASS no-op delete: all final checks true => INCONCLUSIVE / marker_never_seen; reason in HTML')
        r = await replay(OUT / 'delete-noop', config('replay-delete-noop'))
        assert r['verdict'] == 'INCONCLUSIVE' and r['verdict_reason'] == 'marker_never_seen'
        assert r['model_calls'] == {'planner': 0, 'operator': 0} and r['usd'] == 0
        print('PASS no-op delete replay => INCONCLUSIVE / marker_never_seen; zero model calls')
        # A replay must remeasure visibility rather than trust old step booleans.
        original = json.loads((OUT / 'delete-noop/receipt.json').read_text())
        original['steps'][0]['marker_seen'] = True
        original['verdict'] = 'PASS'
        untrusted = OUT / 'delete-untrusted'
        untrusted.mkdir()
        (untrusted / 'receipt.json').write_text(json.dumps(original))
        r = await replay(untrusted, config('replay-delete-untrusted'))
        assert r['verdict_reason'] == 'marker_never_seen' and r['steps'][0]['marker_seen'] is False
        print('PASS replay recomputes marker visibility; ignores source marker_seen=True')
        r = await run(config('delete-counter', requirement=DELETE))
        assert r['verdict'] == 'PASS' and r['verdict_reason'] is None, r
        assert [s['marker_seen'] for s in r['steps']] == [True, False, False]
        assert all('page_text' not in s for s in r['steps'])
        assert all(r['marker'] not in c['page_text'] for c in r['checkpoints'])
        print('PASS add / delete / 0 items left => PASS; transient marker recorded only as step boolean')
        r = await replay(OUT / 'delete-counter', config('replay-delete-counter'))
        assert r['verdict'] == 'PASS' and r['model_calls'] == {'planner': 0, 'operator': 0}
        assert [s['marker_seen'] for s in r['steps']] == [True, False, False]
        print('PASS add / delete replay => PASS; marker remeasured, zero model calls')
    for mutation in ('extra', 'bad_kind', 'boolean_count', 'duplicate', 'empty'):
        p = mock_plan('reload')
        if mutation == 'extra': p['selector'] = '#items'
        if mutation == 'bad_kind': p['checks'][0]['kind'] = 'javascript'
        if mutation == 'boolean_count': p['checks'][0]['count'] = True
        if mutation == 'duplicate': p['checks'].append(p['checks'][0].copy())
        if mutation == 'empty': p['checks'] = []
        try:
            validate_plan(p)
        except ValueError:
            pass
        else:
            raise AssertionError(mutation)
    print('PASS schema validation rejects extra keys, bad kinds, bool counts, duplicate IDs, empty checks')
    cp = [dict(id='a', source='model', page_text='one one', screenshot='a.png'),
          dict(id='b', source='audit', page_text='one', screenshot='b.png')]
    check = dict(id='n', kind='text_count', text='one', count=1, when='after_each_reload')
    assert evaluate([check], cp, 'model_finished')[0] == 'FAIL'
    print('PASS every checkpoint is evaluated (later success cannot mask earlier failure)')
    absent = dict(id='deleted', kind='text_absent', text='unique-marker', when='end')
    end = [dict(id='end', source='end', page_text='0 items left', screenshot='end.png')]
    args = dict(agent_task='Add unique-marker, then delete it.', marker='unique-marker')
    for checks in ([absent], [dict(absent, kind='text_visible')]):
        verdict, _, reason = evaluate(checks, end, 'model_finished', **args)
        assert verdict == 'INCONCLUSIVE' and reason == 'marker_never_seen'
    assert evaluate([absent], end, 'model_finished', **args, steps=[{'marker_seen': True}])[0] == 'PASS'
    seen = [dict(id='early', source='model', page_text='unique-marker', screenshot='early.png')]
    assert evaluate([absent], seen + end, 'model_finished', **args)[0] == 'PASS'
    assert evaluate([absent], end, 'model_finished', marker='unique-marker', agent_task='Observe counter.')[0] == 'PASS'
    assert evaluate([absent], end, 'error', **args)[0] == 'ERROR'
    assert evaluate([absent], end, 'safety_check', **args)[0] == 'stopped_safety_check'
    print('PASS marker guard: overrides passing/failing checks; accepts step/checkpoint sightings; scoped to task; preserves errors/safety')
    async with async_playwright() as pw:
        with app_server(ROOT / 'demo_app/correct') as url:
            browser, page = await sandbox(pw, url, dict(width=1024, height=768))
            try:
                with app_server(ROOT / 'demo_app/correct') as foreign_url:
                    assert urllib.request.urlopen(foreign_url).status == 200
                    value = await page.evaluate("url => fetch(url, {mode:'no-cors'}).then(()=>false).catch(()=>true)", foreign_url)
                    assert value is True
                driver = Driver(page, url, OUT, {'checkpoints': []}, lambda e: None)
                assert driver.can_confirm()
                await page.goto(url + '?other=page')
                assert not driver.can_confirm()
            finally:
                await browser.close()
    print('PASS foreign-origin requests blocked; confirmations restricted to exact local app pages')
    await asyncio.to_thread(web_smoke)
    await web_browser()
    cli_args = [sys.executable, '-m', 'plainproof', 'check', '--app-dir', str(ROOT / 'demo_app/bug_reload'),
                '--requirement', RELOAD, '--out', str(OUT / 'cli-before'), '--mock']
    proc = await asyncio.create_subprocess_exec(*cli_args, stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    assert proc.returncode == 1 and stdout.startswith(b'FAIL '), stdout
    proc = await asyncio.create_subprocess_exec(sys.executable, '-m', 'plainproof', 'replay',
             str(OUT / 'cli-before'), '--app-dir', str(ROOT / 'demo_app/correct'),
             '--out', str(OUT / 'cli-after'), stdout=asyncio.subprocess.PIPE)
    stdout, _ = await proc.communicate()
    assert proc.returncode == 0 and stdout.startswith(b'PASS '), stdout
    print('PASS python -m plainproof: mock check exits 1/FAIL; model-free replay exits 0/PASS')
    for path in OUT.rglob('*'):
        if path.is_file() and path.suffix in {'.json', '.html'}:
            text = path.read_text()
            assert SENTINEL not in text and 'base64' not in text, path
    print('PASS all receipts: no credential, no base64; HTML includes standalone PNG images')
    print('ALL TESTS PASSED (offline; Playwright headless Chromium only)')


def web_smoke():
    server = make_server(0, OUT / 'web')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def request(path, body=None, headers=None):
        data = json.dumps(body).encode() if body else None
        return urllib.request.urlopen(urllib.request.Request(base + path, data=data,
                   headers=headers or ({'Content-Type': 'application/json'} if body else {})))
    try:
        assert server.server_address[0] == '127.0.0.1'
        assert b'Proof before you accept' in request('/').read()
        body = dict(variant='bug_counter', requirement=COUNTER, mock=True)
        run_id = json.load(request('/api/run', body))['id']
        events = request('/events/' + run_id).read().decode()
        assert '"type": "screenshot"' in events and '"type": "step"' in events
        assert '"verdict": "FAIL"' in events
        assert b'Plainproof' in request('/runs/' + run_id + '/receipt.html').read()
        try:
            request('/api/run', body, {'Content-Type': 'application/json', 'Origin': 'https://foreign.invalid'})
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError('Foreign-origin POST accepted')
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('PASS loopback web UI: POST -> live SSE screenshots/steps -> FAIL -> receipt; cross-origin POST denied')


async def web_browser():
    server = make_server(0, OUT / 'web-browser')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1280, 'height': 1000})
                await page.goto(f'http://127.0.0.1:{server.server_port}')
                await page.locator('#variant').select_option('bug_counter')
                await page.locator('#run').click()
                await page.wait_for_function("document.getElementById('status').textContent === 'FAIL'")
                await page.wait_for_function("document.getElementById('screen').naturalWidth === 1024")
                await page.screenshot(path=str(OUT / 'web-ui.png'), full_page=True)
                await page.locator('#receipt').click()
                await page.wait_for_function("[...document.images].length > 0 && [...document.images].every(i => i.complete && i.naturalWidth === 1024)")
                assert 'FAIL' in await page.title() or 'FAIL' in await page.locator('h1').inner_text()
            finally:
                await browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('PASS rendered web UI: Run -> FAIL -> screenshot -> standalone receipt; all embedded images decode')


if __name__ == '__main__':
    os.environ['MODEL_API_KEY'] = SENTINEL
    asyncio.run(suite())
