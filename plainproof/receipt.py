"""Portable JSON evidence plus a readable, single-file HTML viewer."""
import html
import json
from pathlib import Path
from urllib.parse import quote_from_bytes
from .common import redact


def prepare(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError('Use a fresh, empty output directory')
    (out / 'shots').mkdir()
    return out


def action_text(action):
    """Describe recorded computer actions without exposing their JSON syntax."""
    kind = action['type']
    point = f'({action.get("x", 0)}, {action.get("y", 0)})'
    if kind in {'click', 'double_click', 'move'}:
        return f'{kind.replace("_", " ")} at {point}'
    if kind == 'type':
        return 'type ' + action['text']
    if kind == 'keypress':
        keys = '+'.join(action['keys'])
        reload = {k.upper() for k in action['keys']} in ({'F5'}, {'CTRL', 'R'}, {'CMD', 'R'},
                    {'CONTROL', 'R'}, {'META', 'R'}, {'COMMAND', 'R'})
        return 'press ' + keys + (' → reload' if reload else '')
    if kind == 'scroll':
        return f'scroll by ({action["scroll_x"]}, {action["scroll_y"]}) at {point}'
    if kind == 'drag':
        path = action['path']
        return f'drag from ({path[0]["x"]}, {path[0]["y"]}) to ({path[-1]["x"]}, {path[-1]["y"]})'
    return {'wait': 'wait for the page', 'screenshot': 'take a screenshot'}.get(kind, kind.replace('_', ' '))


def write(out, receipt):
    data = redact(receipt)
    (out / 'receipt.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    def esc(value):
        return html.escape(str(value))

    def shot(name):
        if not name:
            return ''
        # Keep image bytes out of JSON; percent-encoded PNGs travel with the HTML.
        src = 'data:image/png,' + quote_from_bytes((out / name).read_bytes(), safe='')
        return f'<img alt="Screenshot {esc(name)}" src="{src}">'

    verdict = data['verdict']
    tone = {'PASS': 'pass', 'FAIL': 'fail', 'INCONCLUSIVE': 'inconclusive'}.get(verdict, 'stopped')
    label = 'STOPPED' if verdict == 'stopped_safety_check' else verdict
    plan = data.get('resolved_plan') or data.get('planner_output') or {}
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             f'<title>Plainproof receipt · {esc(label)}</title><style>',
             '''*{box-sizing:border-box}body{font:22px/1.45 system-ui,sans-serif;max-width:1220px;
margin:28px auto;padding:0 28px;color:#17283b;background:#f5f7fa}h1,h2,h3,p{margin:0 0 16px}
h1{font-size:60px;line-height:1.1}h2{font-size:34px}h3{font-size:25px}section,header,details{margin:0 0 30px;
background:white;border:1px solid #ccd6e0;border-radius:16px;padding:26px}#verdict{border-left:14px solid;
background:#e5f5ed}#verdict.pass{border-color:#167345}#verdict.fail{border-color:#bb2438;background:#ffebee}
#verdict.inconclusive{border-color:#ac7100;background:#fff3d6}#verdict.stopped{border-color:#657180;background:#e9edf1}
.requirement{font-size:32px;line-height:1.3;font-weight:600}.reason{font-size:25px}.eyebrow{font-weight:600}
table{border-collapse:collapse;width:100%;table-layout:fixed;font-size:22px}th{text-align:left;background:#edf2f7}
th,td{padding:14px 10px;vertical-align:top;border-bottom:1px solid #ccd6e0;overflow-wrap:anywhere}
th:nth-child(1){width:14%}th:nth-child(2){width:24%}th:nth-child(3){width:15%}th:nth-child(4){width:12%}
.badge{display:inline-block;border-radius:8px;padding:3px 9px;font-weight:750;font-size:20px}
.pass .badge,.badge.pass{color:#075431;background:#d3f4e2}.badge.fail{color:#8e1426;background:#ffe0e5}
.badge.inconclusive{color:#795100;background:#fff0c2}mark{background:#ffe08a;color:#17283b;padding:0 2px}
.evidence{white-space:pre-wrap}.hint{display:block;color:#455c72;font-size:20px;margin-top:8px}
.timeline{list-style:none;padding:0;margin:0}.step{border-left:5px solid #7598b9;padding:16px 0 24px 22px;
margin-left:8px}.step-content{display:grid;grid-template-columns:340px 1fr;gap:24px}.step img{width:340px;
height:auto;border:1px solid #a8b9c9;border-radius:8px}.actions{margin:0 0 14px;padding-left:26px}
.state{font-size:22px;font-weight:600}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:12px 28px}
.metrics p{margin:0;padding:10px;background:#edf2f7;border-radius:8px}pre{white-space:pre-wrap;
overflow-wrap:anywhere;font-size:20px}summary{cursor:pointer;font-weight:650}details img{max-width:100%}
a{color:#125a91}@media(max-width:800px){body{padding:0 12px}.step-content{display:block}.step img{max-width:100%}
section{padding:16px}table{table-layout:auto}.metrics{grid-template-columns:1fr}}''',
             f'</style></head><body><header id="verdict" class="{tone}">',
             '<p class="eyebrow">Plainproof · evidence receipt</p>',
             f'<h1>{esc(label)}</h1><p class="requirement">{esc(data["requirement"])}</p>']
    reason = data.get('verdict_reason')
    if reason:
        words = {'marker_never_seen': "The run's marker never appeared in visible page text."}.get(
            reason, reason.replace('_', ' ').capitalize() + '.')
        parts.append(f'<p class="reason">{esc(words)}</p>')
    if tone == 'stopped':
        parts.append('<p class="reason">' + esc(data.get('stop_reason', verdict).replace('_', ' ')) + '</p>')
    parts += ['</header><section id="checks"><h2>Checks and evidence</h2>',
              '<table><thead><tr><th>Check id</th><th>Expects</th><th>When</th><th>Result</th><th>Evidence snippet</th></tr></thead><tbody>']
    for result in data['check_results']:
        check = result['check']
        needle = check['text']
        kind = check['kind']
        expects = ('count = ' + str(check['count']) + ': ' + needle if kind == 'text_count' else
                   ('visible: ' if kind == 'text_visible' else 'absent: ') + needle)
        source = result.get('source', 'missing')
        when = {'end': 'end', 'model': 'after reload', 'audit': 'audit'}.get(source, 'not observed')
        status = 'PASS' if result['passed'] is True else 'FAIL' if result['passed'] is False else 'MISSING'
        badge = status.lower() if status != 'MISSING' else 'inconclusive'
        evidence = esc(result['evidence']).replace(esc(needle), '<mark>' + esc(needle) + '</mark>')
        if needle not in result['evidence']:
            evidence += f'<span class="hint">Expected text not found: <mark>{esc(needle)}</mark></span>'
        parts.append(f'<tr data-check-id="{esc(check["id"])}" data-source="{esc(source)}">'
                     f'<td>{esc(check["id"])}</td><td>{esc(expects)}</td><td>{esc(when)}'
                     f'<span class="hint">{esc(result.get("checkpoint") or "No checkpoint")}</span></td>'
                     f'<td><span class="badge {badge}">{status}</span></td><td class="evidence">{evidence}</td></tr>')
    parts += ['</tbody></table></section>',
              '<section id="steps"><h2>What the operator did</h2><ol class="timeline">']
    for i, step in enumerate(data['steps'], 1):
        seen = str(step.get('marker_seen', False)).lower()
        actions = step.get('executed', [])
        parts += [f'<li class="step" data-step="{i}" data-marker-seen="{seen}"><h3>Step {i}</h3>',
                  '<div class="step-content">', shot(step.get('screenshot')), '<div><ul class="actions">']
        parts += ['<li>' + esc(action_text(a)) + '</li>' for a in actions]
        if not actions:
            parts.append('<li>No actions executed</li>')
        parts += ['</ul>', f'<p class="state">marker_seen: {seen}</p>',
                  f'<p>Acknowledged confirmations: {len(step.get("acknowledgements", []))}</p>', '</div></div></li>']
    parts += ['</ol><p>Total acknowledged confirmations: ' + str(data.get('acknowledged_confirmations', 0)) + '</p></section>',
              '<section id="plan"><h2>Plan</h2><p>' + esc(plan.get('agent_task', 'No operator task was produced.')) + '</p></section>',
              '<section id="run-details"><h2>Run details</h2><div class="metrics">']
    calls = data.get('model_calls', {})
    models = data.get('models', {})
    for role in ('planner', 'operator'):
        parts.append(f'<p>{role.capitalize()} model: {esc(models.get(role, "—"))}</p>')
    parts += [f'<p id="model-calls">Model calls: {sum(calls.values())} · planner {calls.get("planner", 0)} · operator {calls.get("operator", 0)}</p>',
              f'<p id="usd">USD {esc(data.get("usd", 0))}</p>',
              f'<p>Seconds: {esc(data.get("seconds", 0))}</p>', '</div>']
    if data.get('original_receipt'):
        parts.append(f'<p>Replay of <a href="{esc(data["original_receipt_html"])}">original receipt</a>'
                     f' · original verdict: {esc(data["original_verdict"])}</p>')
    parts += ['</section><details id="raw"><summary>Raw JSON and full screenshots</summary>',
              '<pre>' + esc(json.dumps(data, indent=2, ensure_ascii=False)) + '</pre>',
              '<h2>Initial screenshot</h2>', shot(data.get('initial_screenshot'))]
    for cp in data['checkpoints']:
        parts += [f'<h3>{esc(cp["id"])} · {esc(cp["source"])}</h3>', shot(cp.get('screenshot'))]
    parts += ['</details></body></html>']
    (out / 'receipt.html').write_text('\n'.join(parts), encoding='utf-8')
    return data
