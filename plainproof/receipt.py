"""Portable JSON evidence plus a single-file HTML viewer, with no external resources."""
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


def write(out, receipt):
    data = redact(receipt)
    (out / 'receipt.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    def esc(value):
        return html.escape(str(value))
    def pretty(value):
        return '<pre>' + esc(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'
    def shot(name):
        if not name:
            return ''
        # Percent-encoded PNG bytes make HTML standalone without base64 in any artifact.
        src = 'data:image/png,' + quote_from_bytes((out / name).read_bytes(), safe='')
        return f'<img alt="{esc(name)}" src="{src}">'
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<title>Plainproof receipt</title><style>',
             'body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px;color:#182639}',
             'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f6;padding:16px}',
             'img{width:100%;border:1px solid #ccd5df}article{margin:30px 0}h1{font-size:36px}',
             '</style><h1>Plainproof · ' + esc(data['verdict']) + '</h1>',
             '<p>' + esc(data['requirement']) + '</p>']
    if data.get('verdict_reason'):
        parts.append('<p><strong>Verdict reason: ' + esc(data['verdict_reason']) + '</strong> — '
                     'The task requires the marker, but it never appeared in visible page text '
                     'at any action batch or checkpoint.</p>')
    if data.get('original_receipt'):
        link = esc(data['original_receipt_html'])
        parts.append(f'<p>Replay of <a href="{link}">original receipt</a> · {esc(data["original_sha256"])}</p>')
    parts += ['<h2>Plan</h2>', pretty(data.get('planner_output')), '<h2>Checks and evidence</h2>']
    for result in data['check_results']:
        parts += ['<article>', pretty(result), '</article>']
    parts.append('<h2>Action sequence</h2>')
    for i, step in enumerate(data['steps']):
        parts += [f'<article><h3>Step {i+1}</h3>', pretty(step), shot(step.get('screenshot')), '</article>']
    parts.append('<h2>Checkpoints</h2>')
    for cp in data['checkpoints']:
        parts += [f'<article><h3>{esc(cp["id"])} · {esc(cp["source"])}</h3>',
                  '<pre>' + esc(cp['page_text']) + '</pre>', shot(cp['screenshot']), '</article>']
    parts += ['<h2>Run details</h2>', pretty({k: v for k, v in data.items()
               if k not in {'steps', 'checkpoints', 'check_results', 'planner_output', 'planner_raw'}}), '</html>']
    (out / 'receipt.html').write_text('\n'.join(parts), encoding='utf-8')
    return data
