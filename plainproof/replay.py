"""Execute only recorded, successfully executed actions; never instantiate a model."""
import asyncio
from contextlib import suppress
import hashlib
import json
import os
import time
from pathlib import Path
from playwright.async_api import async_playwright
from .agent import Driver, execute_batch, sandbox
from .common import app_server, redact
from .planner import validate_plan
from .receipt import prepare
from .runner import Config, finish, new_receipt


async def replay(receipt_dir, config, on_event=None):
    source = Path(receipt_dir).resolve() / 'receipt.json'
    raw = source.read_bytes()
    original = json.loads(raw)
    validate_plan(original['resolved_plan'])
    if original.get('format_version') != 1:
        raise ValueError('Unsupported receipt version')
    if config.max_steps < 1 or config.timeout_s <= 0:
        raise ValueError('Invalid replay budgets')
    out = prepare(config.out)
    started = time.monotonic()
    config.requirement = original['requirement']
    receipt = new_receipt(config, 'replay')
    receipt.update(marker=original['marker'], viewport=original['viewport'],
                   planner_output=original['planner_output'], resolved_plan=original['resolved_plan'],
                   models=original['models'], original_sha256=hashlib.sha256(raw).hexdigest(),
                   original_receipt=os.path.relpath(source, out),
                   original_receipt_html=os.path.relpath(source.with_suffix('.html'), out),
                   original_verdict=original['verdict'], model_claim=None,
                   original_model_claim=original.get('model_claim'),
                   original_acknowledgements=[s.get('acknowledgements', []) for s in original['steps']])
    def emit(event):
        if on_event:
            on_event(redact(event))
    stop, browser, driver = 'error', None, None
    try:
        with app_server(config.app_dir, config.app_url) as url:
            receipt['app_url'] = url
            async with async_playwright() as pw:
                try:
                    async with asyncio.timeout(config.timeout_s):
                        browser, page = await sandbox(pw, url, receipt['viewport'])
                        driver = Driver(page, url, out, receipt, emit)
                        async def dismiss(dialog):
                            driver.modal_changed = True
                            await dialog.dismiss()
                        page.on('dialog', dismiss)
                        receipt['initial_screenshot'], _ = await driver.screenshot('initial')
                        stop = original['stop_reason']
                        for index, old_step in enumerate(original['steps']):
                            if index >= config.max_steps:
                                stop = 'max_steps'
                                break
                            # Planned-but-unexecuted actions (including safety stops) never replay.
                            actions = old_step['executed']
                            step = dict(actions=actions, executed=[], errors=[], pending_safety_checks=[],
                                        acknowledgements=[], original_step=index + 1)
                            receipt['steps'].append(step)
                            try:
                                await execute_batch(driver, actions, step)
                            finally:
                                step['screenshot'], _ = await driver.screenshot('replay')
                                emit({'type': 'step', 'step': step})
                        await driver.checkpoint('end')
                        if stop == 'model_finished':
                            await driver.reload('audit')
                finally:
                    if browser:
                        # Preserve an end checkpoint on planner/action/budget errors too,
                        # when the browser is still usable. Cleanup gets a bounded grace.
                        if driver and not any(p['source'] == 'end' for p in receipt['checkpoints']):
                            with suppress(Exception):
                                async with asyncio.timeout(2):
                                    await driver.checkpoint('end')
                        await browser.close()
    except TimeoutError:
        stop = 'timeout'
    except Exception as exc:
        stop = 'error'
        receipt['errors'].append({'type': type(exc).__name__})
    return finish(out, receipt, stop, started, emit)
