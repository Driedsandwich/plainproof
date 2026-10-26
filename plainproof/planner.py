"""A model proposes checks; a small explicit schema validator rejects bad plans."""
import json
from .common import account, as_dict, image_url, output_text


def check_schema(kind):
    props = {'id': {'type': 'string', 'minLength': 1},
             'kind': {'type': 'string', 'enum': [kind]},
             'text': {'type': 'string', 'minLength': 1},
             'when': {'type': 'string', 'enum': ['after_each_reload', 'end']}}
    if kind == 'text_count':
        props['count'] = {'type': 'integer', 'minimum': 0}
    return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}


PLAN_SCHEMA = {'type': 'object', 'properties': {
    'agent_task': {'type': 'string', 'minLength': 1},
    'checks': {'type': 'array', 'minItems': 1, 'items': {'anyOf': [
        check_schema(k) for k in ('text_visible', 'text_absent', 'text_count')]}}},
    'required': ['agent_task', 'checks'], 'additionalProperties': False}

INSTRUCTIONS = """Plan a UI test from the requirement and first screenshot. Return only the schema.
Use screenshot-driven UI instructions: no DOM selectors, scripts, URLs or hidden state.
Every literal the operator must type MUST be the exact placeholder {MARKER}; use it in
checks too. Describe precise actions and when to reload. For persistence requirements,
confirm the new item appears BEFORE a model reload, then reload using F5, Ctrl+R or Cmd+R.
Checks use case-sensitive literal substring matching against document.body.innerText;
text_count counts non-overlapping occurrences. No regex, CSS, or inferred state.
Use after_each_reload for persistence and end for final-state requirements. Tests start
in a fresh browser context. Include meaningful checks covering the stated requirement;
never use the marker alone to check a counter. Empty or unsupported goals must not be
represented as passing tautologies. The harness performs an additional audit reload.
"""


def validate_schema(value, schema):
    """Validate exactly the JSON Schema subset used by PLAN_SCHEMA (no extra dependency)."""
    if 'anyOf' in schema:
        for choice in schema['anyOf']:
            try:
                validate_schema(value, choice)
                return
            except ValueError:
                pass
        raise ValueError('Plan does not match any check schema')
    typ = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int}[typ]
    if not valid:
        raise ValueError('Plan value has wrong type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError('Plan enum is invalid')
    if typ == 'object':
        if set(value) != set(schema['required']):
            raise ValueError('Plan keys do not match schema')
        for key, child in value.items():
            validate_schema(child, schema['properties'][key])
    elif typ == 'array':
        if len(value) < schema.get('minItems', 0):
            raise ValueError('Plan checks are empty')
        for child in value:
            validate_schema(child, schema['items'])
    elif typ == 'string' and len(value.strip()) < schema.get('minLength', 0):
        raise ValueError('Plan string is empty')
    elif typ == 'integer' and value < schema.get('minimum', 0):
        raise ValueError('Plan count is negative')


def validate_plan(plan):
    validate_schema(plan, PLAN_SCHEMA)
    ids = [c['id'] for c in plan['checks']]
    if len(ids) != len(set(ids)):
        raise ValueError('Check IDs must be unique')
    return plan


def substitute(plan, marker):
    return {**plan, 'agent_task': plan['agent_task'].replace('{MARKER}', marker),
            'checks': [{**c, 'text': c['text'].replace('{MARKER}', marker)} for c in plan['checks']]}


async def plan(create, requirement, screenshot, receipt):
    response = as_dict(await create(
        model='muse-spark-1.3', instructions=INSTRUCTIONS,
        input=[{'role': 'user', 'content': [
            {'type': 'input_text', 'text': requirement},
            {'type': 'input_image', 'image_url': image_url(screenshot)}]}],
        text={'format': {'type': 'json_schema', 'name': 'plainproof_plan',
                         'strict': True, 'schema': PLAN_SCHEMA}}))
    account(receipt, 'planner', response)
    raw = output_text(response)
    receipt['planner_raw'] = raw
    if response.get('status') == 'incomplete':
        raise ValueError('Incomplete planner response')
    return validate_plan(json.loads(raw))
