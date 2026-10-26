"""Plain asserts; pytest is optional. All browser work is local and headless."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plainproof_min', ROOT / 'plainproof_min.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def cli_run(folder, variant='fixed', scenario='normal', extra=()):
    env = {k: v for k, v in os.environ.items() if k not in {'MODEL_API_KEY', 'OPENAI_API_KEY'}}
    process = subprocess.run(
        [sys.executable, str(ROOT / 'plainproof_min.py'), '--mock', '--variant', variant,
         '--out', str(folder), '--mock-scenario', scenario, *extra],
        env=env, capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((folder / 'verdict.json').read_text())
    turns = [json.loads(line) for line in (folder / 'trajectory.jsonl').read_text().splitlines()]
    assert 'base64' not in (folder / 'trajectory.jsonl').read_text()
    assert result['usd'] == 0 and result['usage'] == dict(
        input_tokens=0, cached_input_tokens=0, output_tokens=0)
    for shot in (folder / 'shots').glob('*.png'):
        png = shot.read_bytes()
        assert png[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', png[16:24]) == (1024, 768)
    return result, turns


def test_buggy():
    with tempfile.TemporaryDirectory() as tmp:
        result, turns = cli_run(Path(tmp), 'buggy')
        assert result['verdict'] == 'FAIL' and result['agrees'] is True
        assert result['stop_reason'] == 'model_finished' and result['steps'] == 2
        assert result['reloads'] == [
            dict(source='model', before=True, after=False, added_before=True),
            dict(source='audit', before=False, after=False, added_before=True)]
        assert len(turns) == 3 and all(not t['errors'] for t in turns)


def test_fixed():
    with tempfile.TemporaryDirectory() as tmp:
        result, turns = cli_run(Path(tmp))
        assert result['verdict'] == 'PASS' and result['agrees'] is True
        assert all(r['after'] for r in result['reloads'])
        assert [t['response_id'] for t in turns] == ['resp_mock_0', 'resp_mock_1', 'resp_mock_2']


def test_safety():
    with tempfile.TemporaryDirectory() as tmp:
        result, turns = cli_run(Path(tmp), scenario='safety')
        assert result['verdict'] == 'stopped_safety_check'
        assert result['steps'] == 0 and result['reloads'] == []
        assert result['pending_safety_checks'][0]['id'] == 'csc_mock'
        assert len(turns) == 1 and turns[0]['executed'] == []
        assert turns[0]['pending_safety_checks'] == result['pending_safety_checks']


def test_confirm_check_stops_without_policy():
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = cli_run(Path(tmp), 'buggy', scenario='confirm')
        assert result['verdict'] == 'stopped_safety_check' and result['acknowledged_confirmations'] == 0


def test_confirm_check_acknowledged_with_policy():
    for variant, want in (('buggy', 'FAIL'), ('fixed', 'PASS')):
        with tempfile.TemporaryDirectory() as tmp:
            result, turns = cli_run(Path(tmp), variant, scenario='confirm', extra=('--approve-confirmations',))
            assert result['verdict'] == want, result
            assert result['acknowledged_confirmations'] == 2
            assert [t.get('acknowledged') for t in turns[:2]] == [1, 1]


def test_policy_never_acknowledges_other_codes():
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = cli_run(Path(tmp), 'buggy', scenario='safety', extra=('--approve-confirmations',))
        assert result['verdict'] == 'stopped_safety_check' and result['acknowledged_confirmations'] == 0


def test_never_reload():
    for variant in ('fixed', 'buggy'):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = cli_run(Path(tmp), variant, 'never_reload')
            assert result['verdict'] == 'INCONCLUSIVE' and result['agrees'] is None
            assert [r['source'] for r in result['reloads']] == ['audit']
            assert result['reloads'][0]['before'] is True
            assert result['reloads'][0]['after'] == (variant == 'fixed')


def test_step_limit():
    with tempfile.TemporaryDirectory() as tmp:
        result, turns = cli_run(Path(tmp), extra=('--max-steps', '1'))
        assert result['verdict'] == 'INCONCLUSIVE' and result['stop_reason'] == 'max_steps'
        assert result['steps'] == 1 and result['reloads'] == []
        assert turns[-1]['executed'] == []


def test_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = cli_run(Path(tmp), extra=('--timeout-s', '0.001'))
        assert result['verdict'] == 'INCONCLUSIVE' and result['stop_reason'] == 'timeout'


def patched_run(tmp, model, variant='fixed'):
    args = SimpleNamespace(out=tmp, variant=variant, max_steps=25,
                           timeout_s=30, mock=True, mock_scenario='normal')
    with patch.object(harness, 'MockModel', model):
        return asyncio.run(harness.run(args))


def test_model_claim_does_not_decide_verdict_and_cached_cost():
    class LyingModel(harness.MockModel):
        async def create(self, **request):
            response = await super().create(**request)
            response['usage'] = dict(input_tokens=100, output_tokens=10,
                                     input_tokens_details=dict(cached_tokens=40))
            if response['output'][0]['type'] == 'message':
                response['output'][0]['content'][0]['text'] = 'The item disappeared after the reload.'
            return response

    with tempfile.TemporaryDirectory() as tmp:
        result = patched_run(tmp, LyingModel)
        assert result['verdict'] == 'PASS' and result['agrees'] is False
        assert result['usage'] == dict(input_tokens=300, cached_input_tokens=120, output_tokens=30)
        assert abs(result['usd'] - 0.001344) < 1e-10


def test_action_error_stops_batch():
    class BrokenModel(harness.MockModel):
        def __init__(self, *args):
            super().__init__(*args)
            self.batches[0].insert(0, {'type': 'unknown_action'})

    with tempfile.TemporaryDirectory() as tmp:
        result = patched_run(tmp, BrokenModel)
        turns = [json.loads(line) for line in (Path(tmp) / 'trajectory.jsonl').read_text().splitlines()]
        assert result['verdict'] == 'INCONCLUSIVE'
        assert turns[0]['errors'] == ['ValueError'] and turns[0]['executed'] == []
        assert all(not r['added_before'] for r in result['reloads'])
        assert (Path(tmp) / 'shots' / 'step01.png').exists()


def test_reload_aliases():
    for keys in (['CTRL', 'r'], ['Cmd', 'R']):
        class AliasModel(harness.MockModel):
            def __init__(self, *args):
                super().__init__(*args)
                self.batches[1] = [dict(type='keypress', keys=keys)]
        with tempfile.TemporaryDirectory() as tmp:
            result = patched_run(tmp, AliasModel)
            assert result['verdict'] == 'PASS' and result['reloads'][0]['source'] == 'model'


def test_variants_have_one_defect():
    fixed = (ROOT / 'app/fixed/index.html').read_text().splitlines()
    buggy = (ROOT / 'app/buggy/index.html').read_text().splitlines()
    assert len(fixed) == len(buggy)
    assert [n for n, (a, b) in enumerate(zip(fixed, buggy), 1) if a != b] == [39]


def test_multiple_calls_keep_order_and_matching_outputs():
    class MultiModel(harness.MockModel):
        async def create(self, **request):
            if self.turn == 0:
                response = await super().create(**request)
                response['output'].append(dict(type='computer_call', call_id='call_second',
                                               actions=self.batches[1], pending_safety_checks=[]))
                return response
            assert request['previous_response_id'] == 'resp_mock_0'
            assert [i['call_id'] for i in request['input']] == ['call_mock_0', 'call_second']
            assert all(i['output']['type'] == 'computer_screenshot' for i in request['input'])
            return dict(id='resp_final', output=[dict(type='message', content=[
                dict(type='output_text', text='The item is still listed after the reload.')])])

    with tempfile.TemporaryDirectory() as tmp:
        result = patched_run(tmp, MultiModel)
        assert result['verdict'] == 'PASS' and result['steps'] == 1
        assert (Path(tmp) / 'shots' / 'step01-call02.png').exists()


if __name__ == '__main__':
    tests = [function for name, function in list(globals().items()) if name.startswith('test_')]
    for test in tests:
        test()
        print(f'{test.__name__}: OK')
    print(f'{len(tests)} offline tests passed')
