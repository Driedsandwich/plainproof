"""Offline browser integration and protocol tests. No test framework or Meta calls."""
import asyncio
import copy
import hashlib
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
from plainproof.common import app_server, cost
from plainproof.fixers import MuseCodeFixer, SparkFixer
from plainproof.judge import evaluate
from plainproof.mocks import MockOperator, MockSparkFixer, mock_plan
from plainproof.planner import validate_plan
from plainproof.receipt import action_text, write as write_receipt
from plainproof.replay import replay
from plainproof.repair import SIZE_LIMIT, RepairRejected, repair, snapshot, unique_match, validate_edits
from plainproof.runner import Config, run
from web.server import make_server

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get('PLAINPROOF_TEST_OUT', ROOT / 'test-artifacts')).resolve()
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
        cli_control = await run(config('delete-noop-cli', requirement=DELETE, mock_scenario='noop'))
        assert cli_control['verdict'] == r['verdict']
        assert cli_control['verdict_reason'] == r['verdict_reason']
        assert all(c['passed'] for c in cli_control['check_results'])
        assert not any(s.get('marker_seen') for s in cli_control['steps'])
        print('PASS CLI noop scenario matches the no-op delete test operator')
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
    with patch('openai.AsyncOpenAI', side_effect=AssertionError('Real model calls forbidden')):
        await repair_suite()
        await asyncio.to_thread(web_smoke)
        await receipt_browser()
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
    await repair_cli()
    for path in OUT.rglob('*'):
        if path.is_file() and path.suffix in {'.json', '.html'}:
            text = path.read_text()
            assert SENTINEL not in text and 'meta-credential-sentinel-never-write-5203' not in text and 'base64' not in text, path
    print('PASS all receipts: no credential, no base64; HTML includes standalone PNG images')
    print('ALL TESTS PASSED (offline; Playwright headless Chromium only)')


def file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class ReplyFixer:
    def __init__(self, proposal):
        self.proposal = proposal

    async def propose(self, context, png, app_dir, out, record):
        return copy.deepcopy(self.proposal)


async def repair_suite():
    for variant, prefix in [('bug_reload', 'reload'), ('bug_counter', 'counter')]:
        source = OUT / f'{prefix}-{variant}'
        app = ROOT / 'demo_app' / variant
        before, source_before = file_bytes(app), file_bytes(source)
        requests = []
        async def create(**request):
            requests.append(request)
            assert request['model'] == 'muse-spark-1.3'
            fmt = request['text']['format']
            assert fmt['type'] == 'json_schema' and fmt['strict'] is True
            assert fmt['schema']['properties']['edits']['maxItems'] == 5
            content = request['input'][0]['content']
            assert [c['type'] for c in content] == ['input_text', 'input_image']
            context = json.loads(content[0]['text'])
            original = json.loads(source_before['receipt.json'])
            assert context['resolved_plan'] == original['resolved_plan']
            assert context['requirement'] == original['requirement']
            assert context['failing_checks'] and all(r['passed'] is False for r in context['failing_checks'])
            assert all(isinstance(s, str) and s.startswith('Step ') for s in context['executed_steps'])
            assert context['app_files'] == [{'path': 'index.html', 'text': before['index.html'].decode()}]
            response = await MockSparkFixer().create(**request)
            response['usage'] = dict(input_tokens=1000, output_tokens=200, input_tokens_details={'cached_tokens': 100})
            return response
        conf = config('repair-' + variant, variant)
        conf.approve_confirmations = True
        r = await repair(source, conf, fixer_impl=SparkFixer(create=create), fresh_check=variant == 'bug_reload')
        assert r['verdict'] == 'FIXED', r
        assert len(requests) == 1 and r['model_calls'] == {'fixer': 1} and len(r['edits']) == 1
        assert r['source_sha256'] == hashlib.sha256(source_before['receipt.json']).hexdigest()
        assert r['usd'] == cost(conf.model, r['usage']['fixer'])
        assert file_bytes(app) == before and file_bytes(source) == source_before
        rep = json.loads((Path(conf.out) / 'replay/receipt.json').read_text())
        assert rep['model_calls'] == {'planner': 0, 'operator': 0} and rep['usd'] == 0
        original = json.loads(source_before['receipt.json'])
        assert rep['marker'] == original['marker']
        assert rep['resolved_plan'] == original['resolved_plan']
        assert [s['executed'] for s in rep['steps']] == [s['executed'] for s in original['steps']]
        assert rep['original_sha256'] == r['source_sha256']
        diff = (Path(conf.out) / 'repair.diff').read_text()
        assert '--- index.html' in diff and '+++ index.html' in diff
        if variant == 'bug_reload':
            assert r['fresh_check_verdict'] == 'PASS'
            fresh = r['fresh_check_result']
            assert fresh['mode'] == 'check' and sum(fresh['model_calls'].values()) > 0
            assert fresh['safety_policy']['approve_confirmations'] is True
        print(f'PASS repair {variant} => FIXED; original app and FAIL receipt byte-for-byte unchanged; replay zero calls')

    base = dict(diagnosis='Test repair', risk='Test only',
                edits=[dict(path='index.html', find="items.push({text, done: false});\n  input.value = '';", replace="items.push({text, done: false});\n  save();\n  input.value = '';")])
    app = ROOT / 'demo_app/bug_reload'
    before = file_bytes(app)
    bad_edits = [
        dict(path='../index.html'), dict(path='/index.html'), dict(path='./index.html'),
        dict(path='nested/../index.html'), dict(path='index.py'), dict(path='missing.js'),
        dict(find='save();'), dict(find=''), dict(find='missing literal'),
        dict(replace='x' * 4001), dict(replace='\ud800'), dict(replace=SENTINEL),
        dict(replace='meta-credential-sentinel-never-write-5203'),
        dict(replace='data:image/png;base64,YWJj'),
    ]
    for index, change in enumerate(bad_edits):
        proposal = copy.deepcopy(base)
        proposal['edits'][0].update(change)
        # Ensure a valid earlier edit also remains unapplied on later failure.
        proposal['edits'].insert(0, dict(path='index.html', find='<title>', replace='<title>Unapplied '))
        conf = config(f'repair-reject-{index}', 'bug_reload')
        r = await repair(OUT / 'reload-bug_reload', conf, fixer_impl=ReplyFixer(proposal))
        assert r['verdict'] == 'REPAIR_REJECTED' and r['reason'], r
        assert file_bytes(Path(conf.out) / 'patched_app') == before
        assert not (Path(conf.out) / 'replay').exists()
        assert (Path(conf.out) / 'repair.diff').read_text() == ''
        assert file_bytes(app) == before
    print('PASS repair validation: escaping/absolute/unnormalized/disallowed paths, missing/non-unique find, large/invalid UTF-8 replacement, secrets and image data; atomic rejection')

    proposal = copy.deepcopy(base)
    proposal['edits'] = [dict(path='index.html', find='<title>', replace='<title>Wrong fix ')]
    r = await repair(OUT / 'reload-bug_reload', config('repair-wrong', 'bug_reload'), fixer_impl=ReplyFixer(proposal))
    assert r['verdict'] == 'NOT_FIXED' and r['replay_verdict'] == 'FAIL'
    print('PASS valid but wrong repair => NOT_FIXED')

    # A forged source FAIL cannot turn a no-op trajectory into an accepted fix.
    source = OUT / 'repair-noop-source'
    shutil.copytree(OUT / 'delete-noop', source)
    data = json.loads((source / 'receipt.json').read_text())
    data['verdict'] = 'FAIL'
    data['check_results'][0]['passed'] = False
    write_receipt(source, data)
    r = await repair(source, config('repair-noop'), fixer_impl=ReplyFixer(proposal))
    assert r['verdict'] == 'INCONCLUSIVE' and r['replay_verdict'] == 'INCONCLUSIVE'
    rep = json.loads((OUT / 'repair-noop/replay/receipt.json').read_text())
    assert rep['verdict_reason'] == 'marker_never_seen' and sum(rep['model_calls'].values()) == 0
    print('PASS repair replay retains the marker guard => INCONCLUSIVE')

    # Input guards reject before invoking any fixer.
    guarded = OUT / 'guarded-app'
    for case in ('symlink', 'root-symlink', 'disallowed', 'size', 'encoding', 'fifo'):
        guarded.mkdir()
        (guarded / 'index.html').write_bytes(before['index.html'])
        conf = config('repair-app-' + case, 'bug_reload')
        conf.app_dir = str(guarded)
        if case == 'symlink': (guarded / 'linked.js').symlink_to(app / 'index.html')
        if case == 'root-symlink':
            alias = OUT / 'guarded-alias'
            alias.symlink_to(guarded, target_is_directory=True)
            conf.app_dir = str(alias)
        if case == 'disallowed': (guarded / 'test.py').write_text('unchanged')
        if case == 'size': (guarded / 'large.txt').write_bytes(b'x' * SIZE_LIMIT)
        if case == 'encoding': (guarded / 'bad.txt').write_bytes(b'\xff')
        if case == 'fifo': os.mkfifo(guarded / 'pipe.txt')
        r = await repair(OUT / 'reload-bug_reload', conf)
        assert r['verdict'] == 'REPAIR_REJECTED' and r['model_calls']['fixer'] == 0, r
        shutil.rmtree(guarded)
    conf = config('unused')
    conf.app_dir, conf.out = str(app), str(app / 'forbidden-output')
    try:
        await repair(OUT / 'reload-bug_reload', conf)
    except ValueError:
        assert not Path(conf.out).exists()
    else:
        raise AssertionError('Output inside original app accepted')
    for edits in ([], [base['edits'][0]] * 6):
        try:
            validate_edits(dict(base, edits=edits), snapshot(app), OUT)
        except RepairRejected:
            pass
        else:
            raise AssertionError('Invalid edit count accepted')
    assert not unique_match('aaa', 'aa') and unique_match('baa', 'aa')
    for change in ({'diagnosis': 'x' * 601}, {'risk': 'x' * 301}, {'diagnosis': '\ud800'},
                   {'extra': True}, {'edits': [dict(base['edits'][0], extra=True)]}):
        try:
            validate_edits(dict(base, **change), snapshot(app), app)
        except RepairRejected:
            pass
        else:
            raise AssertionError('Invalid repair schema accepted')
    print('PASS app guards: symlinks, special/disallowed files, 2 MB budget, UTF-8, output inside original app; edit count limits')

    # Exercise the real optional adapter with a fake local executable, never a model.
    fakebin = OUT / 'fakebin'
    fakebin.mkdir()
    binary = fakebin / 'muse'
    script = '''#!{python}
import os, pathlib, sys
assert sys.argv[1:3] == ['exec', '--prompt-file']
assert '--yolo' not in sys.argv and '--sandbox-network' in sys.argv
for flag in ('--disable-approval', '--disable-sandbox', '--trust-workspace', '--approval-mode'):
    assert flag not in sys.argv, flag
assert '--disable-shell' in sys.argv and '--no-foreign-personal-context' in sys.argv
assert os.environ['META_API_KEY']
p = pathlib.Path('index.html')
p.write_text(p.read_text().replace("items.push({{text, done: false}});\\n  input.value = '';", "items.push({{text, done: false}});\\n  save();\\n  input.value = '';"))
import json
print(json.dumps({{'payload_type': 'run.output.delta', 'payload': {{'text': 'Added save() in ' + os.getcwd() + '/index.html.'}}}}))
print(json.dumps({{'payload_type': 'run.usage', 'payload': {{'models': [{{'model': MODEL, 'input_tokens': 1000,
    'cached_tokens': 400, 'output_tokens': 50, 'calls': 2}}]}}}}))
'''.format(python=sys.executable)
    script = script.replace('import json\n', "import json\nMODEL = 'muse-spark-1.3'\n")
    binary.write_text(script)
    binary.chmod(0o755)
    with patch.dict(os.environ, {'PATH': str(fakebin)}):
        try:
            MuseCodeFixer()
        except RuntimeError as exc:
            assert 'not enabled' in str(exc)
        else:
            raise AssertionError('Muse Code enabled without opt-in')
        r = await repair(OUT / 'reload-bug_reload', config('repair-muse', 'bug_reload'), fixer='muse-code',
                         fixer_impl=MuseCodeFixer(enabled=True))
        assert r['verdict'] == 'FIXED' and r['model_calls'] == {'fixer': 2}, r
        assert r['usd'] == round((600 * 1.25 + 400 * .15 + 50 * 4.25) / 1e6, 8), r
        assert r['diagnosis'] == 'Added save() in index.html.', r
        # Usage from another model has no known price here: report it as unknown, never as zero.
        binary.write_text(script.replace("MODEL = 'muse-spark-1.3'", "MODEL = 'other-model'"))
        r = await repair(OUT / 'reload-bug_reload', config('repair-muse-other', 'bug_reload'), fixer='muse-code',
                         fixer_impl=MuseCodeFixer(enabled=True))
        assert r['verdict'] == 'FIXED' and r['usd'] is None and r['usage'] is None, r
        binary.write_text(script)
        for case, mutation in [
            ('extra', "pathlib.Path('extra.py').write_text('disallowed')"),
            ('symlink', "pathlib.Path('extra.js').symlink_to('index.html')"),
            ('delete', "p.unlink()"),
            ('large', "p.write_text('x' * 2000001)"),
        ]:
            binary.write_text(script + mutation + '\n')
            conf = config('repair-muse-' + case, 'bug_reload')
            r = await repair(OUT / 'reload-bug_reload', conf, fixer='muse-code',
                             fixer_impl=MuseCodeFixer(enabled=True))
            assert r['verdict'] == 'REPAIR_REJECTED', r
            assert file_bytes(Path(conf.out) / 'patched_app') == before
        assert file_bytes(app) == before
    with patch.dict(os.environ, {'PATH': ''}):
        conf = config('repair-muse-missing', 'bug_reload')
        conf.mock = False
        r = await repair(OUT / 'reload-bug_reload', conf, fixer='muse-code')
        assert r['verdict'] == 'ERROR' and 'not enabled' in r['reason'], r
    print('PASS opt-in Muse Code adapter (fake binary only): command, environment auth, audited edits, replay and rejected-file rollback; missing binary => ERROR')


async def repair_cli():
    args = [sys.executable, '-m', 'plainproof', 'repair', str(OUT / 'reload-bug_reload'),
            '--app-dir', str(ROOT / 'demo_app/bug_reload'), '--mock']
    for name, extra, expected, label in [
        ('fixed', [], 0, 'FIXED'),
        ('not-fixed', [], 1, 'NOT_FIXED'),
        ('rejected', ['--fixer', 'muse-code'], 2, 'REPAIR_REJECTED'),
    ]:
        run_args = args.copy()
        if name == 'not-fixed':
            # Same recorded reload failure, but a second bug beyond the mock's edit.
            app = OUT / 'cli-two-bugs'
            shutil.copytree(ROOT / 'demo_app/bug_reload', app)
            p = app / 'index.html'
            p.write_text(p.read_text().replace("function save() { localStorage.setItem('todos', JSON.stringify(items)); }", 'function save() {}'))
            run_args[run_args.index('--app-dir') + 1] = str(app)
        proc = await asyncio.create_subprocess_exec(*run_args, '--out', str(OUT / ('cli-repair-' + name)), *extra,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        assert proc.returncode == expected and stdout.startswith(label.encode()), (stdout, stderr)
    print('PASS repair CLI exit codes: FIXED 0, NOT_FIXED 1, REPAIR_REJECTED 2')


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
        for path in ('/api/run', '/api/repair'):
            try:
                request(path, dict(receipt_id=run_id), {'Content-Type': 'application/json', 'Origin': 'https://foreign.invalid'})
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('Foreign-origin POST accepted')
        entered, release = threading.Event(), threading.Event()
        async def paused_repair(*args, **kwargs):
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return await repair(*args, **kwargs)
        with patch('web.server.repair', side_effect=paused_repair):
            repaired_id = json.load(request('/api/repair', dict(receipt_id=run_id)))['id']
            assert entered.wait(5)
            try:
                for path, payload in [('/api/run', body), ('/api/repair', dict(receipt_id=run_id))]:
                    try:
                        request(path, payload)
                    except urllib.error.HTTPError as error:
                        assert error.code == 409
                    else:
                        raise AssertionError('Concurrent run or repair accepted')
            finally:
                release.set()
            events = request('/events/' + repaired_id).read().decode()
        assert '"verdict": "FIXED"' in events and 'zero model calls' in events
        assert b'FIXED' in request('/runs/' + repaired_id + '/repair.html').read()
        assert b'PASS' in request('/runs/' + repaired_id + '/replay/receipt.html').read()
        for payload in (dict(receipt_id='unknown'), dict(receipt_id=repaired_id)):
            try:
                request('/api/repair', payload)
            except urllib.error.HTTPError as error:
                assert error.code == 400
            else:
                raise AssertionError('Repair accepted without a completed FAIL check job')
        for path in ('/api/run', '/api/repair'):
            try:
                request(path, body, {'Content-Type': 'application/json', 'Host': 'foreign.invalid'})
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('Foreign-host POST accepted')
        # CLI-generated receipts have no in-memory web job. They must be served
        # from the same output root, including after server restarts.
        saved = OUT / 'web' / 'replay-saved'
        saved.mkdir()
        (saved / 'receipt.json').write_text('{}')
        (saved / 'receipt.html').write_text('saved CLI receipt')
        assert request('/runs/replay-saved/receipt.html').read() == b'saved CLI receipt'
        for path in ('/runs/../receipt.html', '/runs/replay-saved/../../delete-noop/receipt.html'):
            try:
                request(path)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError('Receipt path escaped web output root')
        try:
            request('/api/run', body, {'Content-Type': 'application/json', 'Origin': 'https://foreign.invalid'})
        except urllib.error.HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError('Foreign-origin POST accepted')
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('PASS loopback web UI: FAIL -> repair -> FIXED; SSE, receipt links, shared job lock, invalid sources, Host/Origin guards')


async def receipt_browser():
    # Exercise the HTML as a standalone file, with arbitrary planner IDs,
    # count checks and HTML-sensitive literals as well as actual run receipts.
    fixture = OUT / 'receipt-format'
    shutil.copytree(OUT / 'delete-counter', fixture)
    data = json.loads((fixture / 'receipt.json').read_text())
    check = dict(id='planner-custom-42', kind='text_count', text='<literal>&', count=2, when='end')
    data['check_results'] = [dict(check=check, source='end', checkpoint='cp-custom', passed=True,
        actual_count=2, evidence='<literal>& <literal>&', covered=True)]
    data['requirement'] = 'Count <literal>& twice.'
    assert write_receipt(fixture, data) == data
    assert json.loads((fixture / 'receipt.json').read_text()) == data
    assert action_text(dict(type='click', x=421, y=164)) == 'click at (421, 164)'
    assert action_text(dict(type='type', text='check-example')) == 'type check-example'
    assert action_text(dict(type='keypress', keys=['F5'])) == 'press F5 → reload'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width':1280, 'height':900})
            for path in sorted(OUT.glob('*/receipt.html')):
                receipt = json.loads(path.with_suffix('.json').read_text())
                await page.goto(path.as_uri())
                for name in ('verdict', 'checks', 'steps', 'plan', 'run-details'):
                    assert await page.locator('#'+name).count() == 1, path
                banner = await page.locator('#verdict').inner_text()
                assert receipt['requirement'] in banner
                assert (receipt['verdict'] if receipt['verdict'] != 'stopped_safety_check' else 'STOPPED') in banner
                if receipt.get('verdict_reason') == 'marker_never_seen':
                    assert 'marker never appeared in visible page text' in banner
                rows = page.locator('#checks tbody tr')
                assert await rows.count() == len(receipt['check_results'])
                for i, result in enumerate(receipt['check_results']):
                    row = rows.nth(i)
                    assert await row.get_attribute('data-check-id') == result['check']['id']
                    assert await row.get_attribute('data-source') == result.get('source', 'missing')
                    assert await row.locator('mark').count() >= 1
                    assert await row.locator('mark').first.inner_text() == result['check']['text']
                    assert float(await row.evaluate('el => parseFloat(getComputedStyle(el).fontSize)')) >= 20
                steps = page.locator('#steps [data-step]')
                assert await steps.count() == len(receipt['steps'])
                for i, step in enumerate(receipt['steps']):
                    item = steps.nth(i)
                    assert await item.get_attribute('data-step') == str(i+1)
                    text = await item.inner_text()
                    assert 'marker_seen: '+str(step.get('marker_seen', False)).lower() in text
                    assert 'Acknowledged confirmations: '+str(len(step.get('acknowledgements', []))) in text
                    for action in step['executed']:
                        assert action_text(action) in text
                task = (receipt.get('resolved_plan') or receipt.get('planner_output') or {}).get('agent_task')
                if task:
                    assert task == await page.locator('#plan p').inner_text()
                assert await page.locator('pre:not(details pre)').count() == 0
                assert await page.locator('details[open]').count() == 0
                assert json.loads(await page.locator('#raw pre').text_content()) == receipt
                assert await page.locator('body > :last-child').get_attribute('id') == 'raw'
                # Both thumbnail and full checkpoint PNGs decode offline.
                await page.wait_for_function('[...document.images].every(i => i.complete && i.naturalWidth > 0)')
                assert await page.locator('img:not([src^="data:image/png,"])').count() == 0
                assert 'base64' not in path.with_suffix('.json').read_text()
                if receipt.get('mode') == 'replay':
                    details = await page.locator('#run-details').inner_text()
                    assert 'Model calls: 0' in details and 'USD 0' in details
                    assert 'original verdict: '+receipt['original_verdict'] in details
                    assert await page.locator('#run-details a').get_attribute('href') == receipt['original_receipt_html']
            await page.goto((fixture / 'receipt.html').as_uri())
            assert 'count = 2: <literal>&' in await page.locator('#checks').inner_text()
            assert await page.locator('#checks mark').count() == 2
        finally:
            await browser.close()
    print('PASS standalone receipts: readable banners/checks/timelines; escaped literal highlights; collapsed raw JSON; embedded PNGs decode')


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
                await page.go_back()
                # Going back may restore or reload the page; start a fresh mock run.
                await page.locator('#variant').select_option('bug_counter')
                await page.locator('#run').click()
                await page.wait_for_function("document.getElementById('status').textContent === 'FAIL'")
                assert await page.locator('#repair').is_visible()
                # Change the selection before repair; the source job's variant wins.
                await page.locator('#variant').select_option('correct')
                await page.locator('#repair').click()
                await page.wait_for_function("document.getElementById('repair-banner').textContent === 'FIXED'")
                assert await page.locator('#repair-result').is_visible()
                assert await page.locator('#repair-diff .added').count() > 0
                assert await page.locator('#repair-diff .removed').count() > 0
                await page.wait_for_function("document.getElementById('screen').src.includes('/replay/') && document.getElementById('screen').complete")
                assert await page.locator('#screen').get_attribute('alt') == 'Patched app after recorded replay'
                await page.locator('#variant').select_option('bug_counter')
                await page.screenshot(path=str(OUT / 'web-repair.png'), full_page=True)
                original_link = await page.locator('#repair-original').get_attribute('href')
                assert original_link and 'receipt.html' in original_link
                await page.locator('#receipt').click()
                assert await page.locator('#verdict h1').inner_text() == 'FIXED'
                assert await page.locator('#diff .added').count() > 0
                await page.locator('#links a').nth(1).click()
                assert await page.locator('#verdict h1').inner_text() == 'PASS'
                assert 'Model calls: 0' in await page.locator('#run-details').inner_text()
            finally:
                await browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('PASS rendered web UI: Run -> FAIL -> Propose a fix -> FIXED banner / diff -> replay PASS, zero model calls; uses original app selection')


if __name__ == '__main__':
    os.environ['MODEL_API_KEY'] = SENTINEL
    os.environ['META_API_KEY'] = 'meta-credential-sentinel-never-write-5203'
    asyncio.run(suite())
