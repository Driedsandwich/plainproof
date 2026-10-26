"""Run orchestration shared by CLI, replay, and the local web interface."""
import asyncio
from contextlib import suppress
import os
import secrets
import time
from dataclasses import dataclass
from playwright.async_api import async_playwright
from . import planner
from .agent import Driver, operate, sandbox
from .common import PRICES, VIEWPORT, app_server, cost, empty_usage, redact
from .judge import claim_verdict, evaluate
from .receipt import prepare, write


@dataclass
class Config:
    out: str
    requirement: str = ''
    app_dir: str | None = None
    app_url: str | None = None
    model: str = 'muse-spark-1.3'
    approve_confirmations: bool = False
    mock: bool = False
    mock_scenario: str = 'normal'
    max_steps: int = 25
    timeout_s: float = 180


def new_receipt(config, mode='check'):
    return dict(format_version=1, mode=mode, requirement=config.requirement,
                marker='check-' + secrets.token_hex(6), viewport=dict(VIEWPORT),
                models={'planner': 'muse-spark-1.3', 'operator': config.model},
                mock=config.mock, planner_output=None, resolved_plan=None,
                model_calls={'planner': 0, 'operator': 0},
                usage={'planner': empty_usage(), 'operator': empty_usage()},
                steps=[], checkpoints=[], check_results=[], model_claim=None, errors=[],
                safety_policy={'approve_confirmations': config.approve_confirmations,
                               'only_code': 'computer_action_requires_confirmation',
                               'local_app_only': True},
                budgets={'max_steps': config.max_steps, 'timeout_s': config.timeout_s})


def finish(out, receipt, stop, started, emit):
    receipt['stop_reason'] = stop
    plan = receipt.get('resolved_plan') or {}
    checks = plan.get('checks', [])
    receipt['verdict'], receipt['check_results'], receipt['verdict_reason'] = evaluate(
        checks, receipt['checkpoints'], stop, agent_task=plan.get('agent_task', ''),
        marker=receipt['marker'], steps=receipt['steps'])
    if not checks and stop not in {'error', 'safety_check'}:
        receipt['verdict'] = 'INCONCLUSIVE'
    interpreted = claim_verdict(receipt['model_claim'])
    receipt['model_claim_verdict'] = interpreted
    receipt['agrees'] = (interpreted == receipt['verdict'] if interpreted and
                         receipt['verdict'] in {'PASS', 'FAIL'} else None)
    receipt['acknowledged_confirmations'] = sum(len(s.get('acknowledgements', [])) for s in receipt['steps'])
    receipt['usd_by_role'] = {role: cost(receipt['models'][role], receipt['usage'][role])
                              for role in ('planner', 'operator')}
    receipt['usd'] = round(sum(receipt['usd_by_role'].values()), 8)
    receipt['seconds'] = round(time.monotonic() - started, 3)
    result = write(out, receipt)
    emit({'type': 'done', 'verdict': result['verdict'], 'receipt': 'receipt.html'})
    return result


async def run(config, on_event=None, planner_create=None, operator_factory=None):
    if config.max_steps < 1 or config.timeout_s <= 0 or config.model not in PRICES:
        raise ValueError('Invalid model or budgets')
    if bool(config.app_dir) == bool(config.app_url) or not config.requirement.strip():
        raise ValueError('Provide one app source and a non-empty requirement')
    out = prepare(config.out)
    started, receipt = time.monotonic(), new_receipt(config)
    def emit(event):
        if on_event:
            on_event(redact(event))
    browser = client = driver = None
    stop = 'error'
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
                        initial, png = await driver.screenshot('initial')
                        receipt['initial_screenshot'] = initial
                        if config.mock:
                            from .mocks import MockPlanner, MockOperator, scenario_for
                            scenario = scenario_for(config.requirement)
                            pc = MockPlanner(scenario, config.mock_scenario == 'invalid_plan').create
                            oc = MockOperator(page, receipt['marker'], scenario, config.mock_scenario).create
                        else:
                            # No credential ever enters a record or a progress event.
                            from openai import AsyncOpenAI
                            client = AsyncOpenAI(base_url='https://api.meta.ai/v1',
                                                 api_key=os.environ['MODEL_API_KEY'], max_retries=2)
                            pc = oc = client.responses.create
                        if planner_create:
                            pc = planner_create
                        if operator_factory:
                            oc = operator_factory(page, receipt['marker']).create
                        emit({'type': 'status', 'message': 'Planning checks'})
                        receipt['planner_output'] = await planner.plan(pc, config.requirement, png, receipt)
                        receipt['resolved_plan'] = planner.substitute(receipt['planner_output'], receipt['marker'])
                        emit({'type': 'plan', 'plan': receipt['resolved_plan']})
                        stop = await operate(oc, driver, receipt['resolved_plan']['agent_task'], png, receipt, config)
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
        # Exception bodies can contain credentials, request bodies, or data URLs.
        # Persist only type and HTTP status, never their free-form messages.
        receipt['errors'].append({'type': type(exc).__name__, 'status': getattr(exc, 'status_code', None)})
    finally:
        if client:
            await client.close()
    return finish(out, receipt, stop, started, emit)
