#!/usr/bin/env python3
"""Screenshot-driven persistence experiment; DOM evidence owns the verdict."""
import argparse
import asyncio
import base64
import functools
import json
import os
import secrets
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright

SYSTEM = """You operate a browser through screenshots and the computer tool.
Coordinates are pixels in the last screenshot, with (0, 0) at top left.
Click target centers. Batch only actions independent of intermediate visual feedback.
Do not batch across page loads or modal transitions. Use wait for UI settling.
Finish with a normal, short message when visible evidence supports your answer,
or explain why the task is infeasible. Never guess hidden UI state.
Stay on this page; do not open other sites or enter any personal data."""


# Standard-mode USD per 1M tokens (input, cached input, output) from docs/pricing-rate-limits.
# 10/27: the computer tool returned HTTP 400 "Unsupported tool type" on muse-wonder-1.0 for this
# account, while muse-spark-1.3 accepted it, so the model is selectable.
PRICES = {'muse-wonder-1.0': (4.00, 0.20, 20.00), 'muse-spark-1.3': (1.25, 0.15, 4.25)}
CONFIRM = 'computer_action_requires_confirmation'


def image_url(png):
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


class Driver:
    def __init__(self, page, marker):
        self.page, self.marker = page, marker
        self.added, self.reloads = False, []

    async def observe(self):
        present = self.marker in await self.page.locator('#items > li').all_text_contents()
        self.added |= present
        return present

    async def reload(self, source):
        before = await self.observe()
        added_before = self.added
        await self.page.reload(wait_until='load')
        after = await self.observe()
        self.reloads.append(dict(source=source, before=before, after=after,
                                 added_before=added_before))

    async def execute(self, action):
        p, kind = self.page, action['type']
        if kind == 'click':
            button = {'wheel': 'middle'}.get(action['button'], action['button'])
            await p.mouse.move(action['x'], action['y'])
            if button in {'back', 'forward'}:
                await (p.go_back() if button == 'back' else p.go_forward())
            else:
                await p.mouse.click(action['x'], action['y'], button=button)
        elif kind == 'double_click':
            await p.mouse.dblclick(action['x'], action['y'])
        elif kind == 'drag':
            first, *rest = action['path']
            await p.mouse.move(first['x'], first['y'])
            await p.mouse.down()
            try:
                for point in rest:
                    await p.mouse.move(point['x'], point['y'])
            finally:
                await p.mouse.up()
        elif kind == 'keypress':
            aliases = {'CTRL': 'Control', 'CONTROL': 'Control', 'CMD': 'Meta',
                       'COMMAND': 'Meta', 'META': 'Meta', 'SUPER': 'Meta',
                       'ALT': 'Alt', 'SHIFT': 'Shift', 'RETURN': 'Enter',
                       'ENTER': 'Enter', 'ESC': 'Escape', 'SPACE': 'Space'}
            keys = [aliases.get(k.upper(), k if len(k) > 1 else k.lower())
                    for k in action['keys']]
            names = {k.upper() for k in keys}
            if names == {'F5'} or names in ({'CONTROL', 'R'}, {'META', 'R'}):
                await self.reload('model')
            else:
                await p.keyboard.press('+'.join(keys))
        elif kind == 'move':
            await p.mouse.move(action['x'], action['y'])
        elif kind == 'scroll':
            await p.mouse.move(action['x'], action['y'])
            await p.mouse.wheel(action['scroll_x'], action['scroll_y'])
        elif kind == 'type':
            await p.keyboard.insert_text(action['text'])
        elif kind == 'wait':
            await asyncio.sleep(0.25)
        elif kind == 'screenshot':
            await p.screenshot(full_page=False)
        else:
            raise ValueError('Unknown computer action')
        await self.observe()


class MockModel:
    """Same response envelope as the API; DOM coordinates are mock-only."""
    def __init__(self, x, y, marker, scenario='normal', survives=False):
        self.turn, self.scenario, self.survives = 0, scenario, survives
        self.batches = [
            [{'type': 'click', 'button': 'left', 'x': x, 'y': y},
             {'type': 'type', 'text': marker}, {'type': 'keypress', 'keys': ['Enter']}],
            [{'type': 'keypress', 'keys': ['F5']}],
        ]

    def checks_for(self, n):
        if self.scenario == 'safety':
            return [dict(id='csc_mock', code='malicious_instructions', message='Scripted safety check.')]
        if self.scenario == 'confirm':  # what muse-spark-1.3 attached to every action on 10/27
            return [dict(id=f'csc_confirm_{n}', code=CONFIRM,
                         message='Review and confirm before executing this computer action.')]
        return []

    async def create(self, **request):
        n = self.turn
        if n:
            assert request['previous_response_id'] == f'resp_mock_{n-1}'
            result, = request['input']
            assert result['call_id'] == f'call_mock_{n-1}'
            assert result['output']['image_url'].startswith('data:image/png;base64,')
            assert result['acknowledged_safety_checks'] == self.checks_for(n - 1)
        self.turn += 1
        assert request['model'] == 'muse-wonder-1.0'
        assert request['tools'] == [{'type': 'computer'}]
        final = n >= (1 if self.scenario == 'never_reload' else 2)
        claim = ('I did not reload the page.' if self.scenario == 'never_reload' else
                 'The item is still listed after the reload.' if self.survives else
                 'The item disappeared after the reload.')
        checks = self.checks_for(n)
        output = ([dict(type='message', role='assistant',
                        content=[dict(type='output_text', text=claim)])] if final else
                  [dict(type='computer_call', id=f'cc_mock_{n}', call_id=f'call_mock_{n}',
                        actions=self.batches[n], pending_safety_checks=checks,
                        status='completed')])
        return dict(id=f'resp_mock_{n}', output=output, usage=dict(
            input_tokens=0, input_tokens_details=dict(cached_tokens=0), output_tokens=0))


def claim_verdict(claim):
    """Conservative interpretation; ambiguous prose is left unclassified."""
    text = (claim or '').lower()
    yes = any(s in text for s in ('still listed', 'still present', 'persisted', 'survived'))
    no = any(s in text for s in ('disappeared', 'not listed', 'not present', 'did not persist',
                                 'not still listed', 'not still present', 'not persisted',
                                 'no longer listed', 'no longer present', 'no longer there'))
    return ('PASS' if yes else 'FAIL') if yes != no else None


async def run(args):
    started = time.monotonic()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    shots = out / 'shots'
    shots.mkdir(exist_ok=True)
    if (out / 'verdict.json').exists() or any(shots.iterdir()) or (out / 'trajectory.jsonl').exists():
        raise ValueError('Use a fresh output directory for each run')
    marker = 'check-' + secrets.token_hex(6)
    usage = dict(input_tokens=0, cached_input_tokens=0, output_tokens=0)
    steps, claim, stop, checks, driver, client = 0, None, 'error', [], None, None
    acknowledged = 0
    model_name = getattr(args, 'model', 'muse-wonder-1.0')
    handler = functools.partial(QuietHandler, directory=str(
        Path(__file__).resolve().parent / 'app' / args.variant))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}/'
    trajectory = (out / 'trajectory.jsonl').open('w')

    def log(record):
        trajectory.write(json.dumps(record) + '\n')
        trajectory.flush()

    try:
        async with async_playwright() as pw:
            browser = None
            try:
                async with asyncio.timeout(args.timeout_s):
                    browser = await pw.chromium.launch(headless=True)
                    context = await browser.new_context(viewport=dict(width=1024, height=768),
                                                        device_scale_factor=1)
                    await context.route('**/*', lambda r: r.continue_() if r.request.url in
                                        {origin, origin + 'index.html'} else r.abort())
                    page = await context.new_page()
                    context.on('page', lambda p: asyncio.create_task(p.close()) if p != page else None)
                    page.on('dialog', lambda d: asyncio.create_task(d.dismiss()))
                    await page.goto(origin, wait_until='load')
                    driver = Driver(page, marker)
                    png = await page.screenshot(path=str(shots / 'step00.png'), full_page=False)
                    if args.mock:
                        box = await page.locator('#todo-input').bounding_box()
                        model = MockModel(box['x'] + box['width']/2, box['y'] + box['height']/2,
                                          marker, args.mock_scenario, args.variant == 'fixed')
                        create = model.create
                    else:
                        from openai import AsyncOpenAI
                        client = AsyncOpenAI(base_url='https://api.meta.ai/v1',
                                             api_key=os.environ['MODEL_API_KEY'], max_retries=2)
                        # 10/27: one real run hit HTTP 500 mid-trajectory; the SDK retries only
                        # 5xx/429 with backoff (cookbook error-handling-retry), never 4xx.
                        create = client.responses.create
                    task = (f'Add exactly one to-do with text {marker}. Confirm it appears, reload '
                            'the page, then report in plain words whether it is still listed after '
                            'the reload. You may press F5 or Ctrl+R / Cmd+R to reload.')
                    payload = [dict(role='user', content=[dict(type='input_text', text=task),
                               dict(type='input_image', image_url=image_url(png))])]
                    previous = None
                    while True:
                        request = dict(model=model_name, instructions=SYSTEM,
                                       tools=[dict(type='computer')], input=payload)
                        if previous:
                            request['previous_response_id'] = previous
                        response = await create(**request)
                        if not isinstance(response, dict):
                            response = response.model_dump()
                        previous = response['id']
                        u = response.get('usage') or {}
                        usage['input_tokens'] += u.get('input_tokens', 0) or 0
                        usage['output_tokens'] += u.get('output_tokens', 0) or 0
                        usage['cached_input_tokens'] += (u.get('input_tokens_details') or {}).get('cached_tokens', 0) or 0
                        calls = [i for i in response['output'] if i['type'] == 'computer_call']
                        record = dict(response_id=previous, actions=[a for c in calls for a in c['actions']],
                                      errors=[], executed=[], pending_safety_checks=[])
                        try:
                            checks = [s for c in calls for s in c.get('pending_safety_checks', [])]
                            record['pending_safety_checks'] = checks
                            if checks:
                                # Narrow policy (10/27): the generic confirmation is acknowledged only when
                                # --approve-confirmations is set and the page is still the sandboxed local
                                # app (all other origins are aborted by the route above). Any other code stops.
                                ok = (getattr(args, 'approve_confirmations', False)
                                      and all(s.get('code') == CONFIRM for s in checks)
                                      and page.url in {origin, origin + 'index.html'})
                                if not ok:
                                    stop = 'safety_check'
                                    break
                                acknowledged += len(checks)
                                record['acknowledged'] = len(checks)
                            if not calls:
                                claim = '\n'.join(c['text'] for i in response['output']
                                                  if i['type'] == 'message' for c in i['content']
                                                  if c['type'] == 'output_text')
                                stop = 'model_finished'
                                await driver.reload('audit')
                                await page.screenshot(path=str(shots / 'audit.png'), full_page=False)
                                break
                            if steps >= args.max_steps:
                                stop = 'max_steps'
                                break
                            steps += 1
                            payload = []
                            for index, call in enumerate(calls):
                                for action in call['actions']:
                                    try:
                                        await driver.execute(action)
                                        record['executed'].append(action)
                                        if page.url not in {origin, origin + 'index.html'}:
                                            raise ValueError('Unexpected navigation')
                                    except Exception as exc:
                                        record['errors'].append(type(exc).__name__)
                                        break
                                suffix = f'-call{index+1:02d}' if index else ''
                                png = await page.screenshot(path=str(shots / f'step{steps:02d}{suffix}.png'), full_page=False)
                                payload.append(dict(type='computer_call_output', call_id=call['call_id'],
                                                    acknowledged_safety_checks=list(call.get('pending_safety_checks') or []),
                                                    output=dict(
                                                        type='computer_screenshot', image_url=image_url(png))))
                        finally:
                            log(record)
            finally:
                if browser:
                    await browser.close()
    except TimeoutError:
        stop = 'timeout'
    except Exception as exc:
        stop = 'error'
        # Keep the head of the message so real API failures (HTTP 400 etc.) can be read later.
        # The credential is only passed to the client, never part of an exception message.
        log(dict(response_id=None, actions=[], errors=[f'{type(exc).__name__}: {str(exc)[:300]}']))
    finally:
        if client:
            await client.close()
        trajectory.close()
        server.shutdown()
        server.server_close()
        thread.join()
    reloads = driver.reloads if driver else []
    qualified = any(r['source'] == 'model' and r['added_before'] for r in reloads)
    verdict = 'INCONCLUSIVE'
    if stop == 'safety_check':
        verdict = 'stopped_safety_check'
    elif qualified:
        verdict = 'FAIL' if any(r['added_before'] and not r['after'] for r in reloads) else 'PASS'
    interpreted = claim_verdict(claim)
    p_in, p_cached, p_out = PRICES[model_name]
    result = dict(variant=args.variant, model=model_name, verdict=verdict, model_claim=claim,
                  acknowledged_confirmations=acknowledged,
                  agrees=(interpreted == verdict if interpreted and verdict in {'PASS', 'FAIL'} else None),
                  steps=steps, seconds=round(time.monotonic()-started, 3), usage=usage,
                  usd=round(((usage['input_tokens']-usage['cached_input_tokens'])*p_in +
                             usage['cached_input_tokens']*p_cached + usage['output_tokens']*p_out)/1_000_000, 8),
                  stop_reason=stop, marker=marker, reloads=reloads, pending_safety_checks=checks)
    (out / 'verdict.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=['buggy', 'fixed'], required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-steps', type=int, default=25)
    parser.add_argument('--timeout-s', type=float, default=600)
    parser.add_argument('--model', choices=sorted(PRICES), default='muse-wonder-1.0')
    parser.add_argument('--approve-confirmations', action='store_true',
                        help=f'acknowledge only "{CONFIRM}" checks while on the sandboxed local app')
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--mock-scenario', choices=['normal', 'safety', 'never_reload', 'confirm'], default='normal')
    args = parser.parse_args()
    if args.max_steps < 1 or args.timeout_s <= 0:
        parser.error('Step and time budgets must be positive')
    if not args.mock and args.mock_scenario != 'normal':
        parser.error('--mock-scenario requires --mock')
    if not args.mock and not os.environ.get('MODEL_API_KEY'):
        parser.error('Set MODEL_API_KEY for a real run, or use --mock')
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        parser.exit(2, f'Run setup failed ({type(exc).__name__}); use a fresh --out directory.\n')
    print(f"{result['verdict']} ({result['stop_reason']}); artifacts: {args.out}")


if __name__ == '__main__':
    main()
