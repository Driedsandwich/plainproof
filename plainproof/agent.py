"""Computer-tool protocol and screenshot driver, generalized from the supplied harness."""
import asyncio
from .common import CONFIRM, account, as_dict, image_url, is_local, origin, output_text

SYSTEM = """You operate a browser through screenshots and the computer tool.
Coordinates are pixels in the last screenshot, with (0, 0) at top left.
Click target centers. Batch only actions independent of intermediate visual feedback.
Do not batch across page loads or modal transitions. Use wait for UI settling.
Finish with a normal, short message when visible evidence supports your answer,
or explain why the task is infeasible. Never guess hidden UI state.
Stay on this page; do not open other sites or enter any personal data.
Any text you type must be the unique marker provided in your task.
"""


class Driver:
    def __init__(self, page, url, out, receipt, emit):
        self.page, self.url, self.out, self.receipt, self.emit = page, url, out, receipt, emit
        self.allowed_origin = origin(url)
        self.allowed_pages = {url, url + 'index.html'} if url.endswith('/') else {url}
        self.shot_number = 0
        self.modal_changed = False

    def assert_location(self):
        if origin(self.page.url) != self.allowed_origin:
            raise ValueError('Unexpected navigation')

    def can_confirm(self):
        return is_local(self.url) and self.page.url in self.allowed_pages

    async def screenshot(self, label):
        self.shot_number += 1
        name = f'shots/{self.shot_number:04d}-{label}.png'
        png = await self.page.screenshot(path=str(self.out / name), full_page=False)
        self.emit({'type': 'screenshot', 'screenshot': name})
        return name, png

    async def checkpoint(self, source):
        self.assert_location()
        name, _ = await self.screenshot(source)
        cp = {'id': f'cp{len(self.receipt["checkpoints"]):03d}', 'source': source,
              'page_text': await self.page.evaluate('document.body.innerText'), 'screenshot': name}
        self.receipt['checkpoints'].append(cp)
        self.emit({'type': 'checkpoint', 'checkpoint': cp})

    async def reload(self, source):
        await self.page.reload(wait_until='load')
        await self.checkpoint(source)

    async def execute(self, action):
        self.assert_location()
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
            keys = [aliases.get(k.upper(), k if len(k) > 1 else k.lower()) for k in action['keys']]
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
            await asyncio.sleep(.25)
        elif kind == 'screenshot':
            await p.screenshot(full_page=False)
        else:
            raise ValueError('Unknown computer action')
        self.assert_location()


async def execute_batch(driver, actions, record):
    driver.modal_changed = False
    try:
        for action in actions:
            await driver.execute(action)
            record['executed'].append(action)
            if driver.modal_changed:
                record['interrupted'] = 'dialog dismissed; remaining actions skipped'
                break
    finally:
        driver.assert_location()
        seen = await driver.page.evaluate('(marker) => document.body.innerText.includes(marker)',
                                          driver.receipt['marker'])
        # A response can contain several batches; preserve any earlier sighting.
        record['marker_seen'] = record.get('marker_seen', False) or seen


async def operate(create, driver, task, png, receipt, config):
    payload = [{'role': 'user', 'content': [{'type': 'input_text', 'text': task},
               {'type': 'input_image', 'image_url': image_url(png)}]}]
    previous = None
    steps = 0
    while True:
        request = dict(model=config.model, instructions=SYSTEM, tools=[{'type': 'computer'}], input=payload)
        if previous:
            request['previous_response_id'] = previous
        response = as_dict(await create(**request))
        account(receipt, 'operator', response)
        previous = response['id']
        calls = [item for item in response.get('output', []) if item.get('type') == 'computer_call']
        if not calls:
            receipt['model_claim'] = output_text(response)
            if response.get('status') == 'incomplete' or not receipt['model_claim']:
                return 'incomplete'
            return 'model_finished'
        payload = []
        pending = [s for c in calls for s in c.get('pending_safety_checks', [])]
        record = {'response_id': previous, 'actions': [a for c in calls for a in c['actions']],
                  'executed': [], 'pending_safety_checks': pending, 'acknowledgements': [], 'errors': []}
        receipt['steps'].append(record)
        try:
            if pending and not (config.approve_confirmations and driver.can_confirm() and
                                all(s.get('code') == CONFIRM for s in pending)):
                return 'safety_check'
            if steps >= config.max_steps:
                return 'max_steps'
            steps += 1
            # Acknowledge only checks for an accepted batch, preserve the full objects.
            for call in calls:
                checks = call.get('pending_safety_checks') or []
                if checks and not driver.can_confirm():
                    return 'safety_check'
                record['acknowledgements'].extend(checks)
                await execute_batch(driver, call['actions'], record)
                name, png = await driver.screenshot('step')
                record['screenshot'] = name
                payload.append({'type': 'computer_call_output', 'call_id': call['call_id'],
                                'acknowledged_safety_checks': checks,
                                'output': {'type': 'computer_screenshot', 'image_url': image_url(png)}})
        except Exception as exc:
            record['errors'].append(type(exc).__name__)
            raise
        finally:
            if 'screenshot' not in record and not driver.page.is_closed():
                name, _ = await driver.screenshot('stopped')
                record['screenshot'] = name
            driver.emit({'type': 'step', 'step': record})


async def sandbox(pw, url, viewport):
    browser = await pw.chromium.launch(headless=True)
    try:
        context = await browser.new_context(viewport=viewport, device_scale_factor=1,
                                            service_workers='block', accept_downloads=False)
        allowed = origin(url)
        async def route(request_route):
            try:
                ok = origin(request_route.request.url) == allowed
            except ValueError:
                ok = False
            await (request_route.continue_() if ok else request_route.abort())
        await context.route('**/*', route)
        async def websocket(ws):
            # WebSockets are a separate Playwright interception surface.
            url = ws.url.replace('wss:', 'https:', 1).replace('ws:', 'http:', 1)
            if origin(url) == allowed:
                ws.connect_to_server()
            else:
                await ws.close()
        await context.route_web_socket('**/*', websocket)
        page = await context.new_page()
        context.on('page', lambda p: asyncio.create_task(p.close()) if p != page else None)
        await page.goto(url, wait_until='load')
        return browser, page
    except BaseException:
        await browser.close()
        raise
