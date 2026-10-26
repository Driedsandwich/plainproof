"""Offline protocol doubles. DOM coordinates are deliberately mock-only."""
import json
from .common import CONFIRM


def scenario_for(requirement):
    text = requirement.lower()
    if 'counter' in text or 'items left' in text:
        return 'counter'
    if 'delete' in text or 'remove' in text:
        return 'delete'
    return 'reload'


def mock_plan(scenario):
    task = 'Add exactly one to-do with text {MARKER}. Confirm it appears. '
    if scenario == 'counter':
        task += 'Mark that item done with its checkbox; verify the counter reads 0 items left.'
        checks = [dict(id='counter', kind='text_visible', text='0 items left', when='end'),
                  dict(id='added', kind='text_count', text='{MARKER}', count=1, when='end')]
    elif scenario == 'delete':
        task += 'Delete that item, then reload with F5 and report whether it is absent.'
        checks = [dict(id='deleted', kind='text_absent', text='{MARKER}', when='after_each_reload')]
    else:
        task += 'Reload using F5, then report whether the item is still listed.'
        checks = [dict(id='persist', kind='text_count', text='{MARKER}', count=1, when='after_each_reload')]
    return dict(agent_task=task, checks=checks)


class MockPlanner:
    def __init__(self, scenario, invalid=False):
        self.scenario, self.invalid, self.calls = scenario, invalid, 0

    async def create(self, **request):
        self.calls += 1
        assert request['model'] == 'muse-spark-1.3'
        assert request['text']['format']['type'] == 'json_schema'
        assert 'response_format' not in request
        return {'output': [{'type': 'message', 'content': [{'type': 'output_text',
                'text': '{bad json' if self.invalid else json.dumps(mock_plan(self.scenario))}]}], 'usage': {}}


class MockOperator:
    def __init__(self, page, marker, scenario, safety='normal', reload_key='F5'):
        self.page, self.marker, self.scenario, self.safety = page, marker, scenario, safety
        self.turn, self.calls, self.last_checks = 0, 0, []
        self.reload_key = reload_key

    async def click(self, selector):
        box = await self.page.locator(selector).bounding_box()
        return dict(type='click', button='left', x=box['x'] + box['width']/2, y=box['y'] + box['height']/2)

    async def create(self, **request):
        n = self.turn
        self.turn += 1
        self.calls += 1
        assert request['tools'] == [{'type': 'computer'}]
        if n:
            assert request['previous_response_id'] == f'mock-{n-1}'
            result, = request['input']
            assert result['call_id'] == f'call-{n-1}'
            assert result['acknowledged_safety_checks'] == self.last_checks
            assert result['output']['image_url'].startswith('data:image/png;base64,')
        actions = None
        if n == 0:
            actions = [await self.click('#todo-input'), dict(type='type', text=self.marker),
                       dict(type='keypress', keys=['Enter'])]
        elif n == 1 and self.safety != 'never_reload':
            if self.scenario == 'counter':
                actions = [await self.click('#items input[type=checkbox]')]
            elif self.scenario == 'delete':
                actions = [await self.click('#items button')]
            else:
                actions = [dict(type='keypress', keys=self.reload_key.split('+'))]
        elif n == 2 and self.scenario == 'delete':
            actions = [dict(type='keypress', keys=['F5'])]
        if actions is None:
            # Deliberately optimistic: proves claims cannot override DOM failures.
            output = [{'type': 'message', 'content': [{'type': 'output_text',
                       'text': 'The counter is correct.' if self.scenario == 'counter' else 'The item is still listed.'}]}]
        else:
            checks = []
            if self.safety in {'safety', 'confirm'}:
                checks = [dict(id=f'check-{n}', code=CONFIRM if self.safety == 'confirm' else 'malicious_instructions',
                               message='Scripted safety check.')]
            self.last_checks = checks
            output = [dict(type='computer_call', call_id=f'call-{n}', actions=actions, pending_safety_checks=checks)]
        return dict(id=f'mock-{n}', output=output, usage={})
