"""Validate a small repair in an isolated copy, then let model-free replay decide."""
import asyncio
import difflib
import hashlib
import html
import json
import os
import stat
import time
from pathlib import Path, PurePosixPath
from .common import PRICES, cost, empty_usage, redact
from .fixers import MuseCodeFixer, SparkFixer
from .planner import validate_plan
from .receipt import action_text, prepare
from .replay import replay
from .runner import Config, run

ALLOWED = {'.html', '.js', '.css', '.json', '.txt'}
SIZE_LIMIT = 2_000_000


class RepairRejected(ValueError):
    pass


def snapshot(root):
    """Read regular UTF-8 app files without following links or special files."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise RepairRejected('App must be a directory, not a symlink')
    files, total = {}, 0
    def walk(directory):
        nonlocal total
        for path in sorted(directory.iterdir()):
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise RepairRejected('App contains a symlink')
            if stat.S_ISDIR(mode):
                walk(path)
                continue
            if not stat.S_ISREG(mode) or path.suffix not in ALLOWED:
                raise RepairRejected('App contains a non-regular or disallowed file')
            # Bound reads as well as total size; also catches a file growing mid-read.
            if path.stat().st_size > SIZE_LIMIT - total:
                raise RepairRejected('App exceeds the 2 MB size limit')
            with path.open('rb') as stream:
                raw = stream.read(SIZE_LIMIT - total + 1)
            total += len(raw)
            if total > SIZE_LIMIT:
                raise RepairRejected('App exceeds the 2 MB size limit')
            try:
                files[path.relative_to(root).as_posix()] = raw.decode('utf-8')
            except UnicodeError:
                raise RepairRejected('App file is not UTF-8') from None
    walk(root)
    return files


def validate_path(value, files, root):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise RepairRejected('Edit path must be a normalized relative app path')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or path.as_posix() != value or ':' in value:
        raise RepairRejected('Edit path must be a normalized relative app path')
    if value not in files or path.suffix not in ALLOWED:
        raise RepairRejected('Edit must target an existing allowed app file')
    target = root / value
    if root.resolve() not in target.resolve().parents or any(p.is_symlink() for p in (target, *target.parents)):
        raise RepairRejected('Edit path escapes the patched app or follows a symlink')
    if not target.is_file():
        raise RepairRejected('Edit must target an existing allowed app file')


def unique_match(text, find):
    at = text.find(find)
    return bool(find) and at >= 0 and text.find(find, at + 1) < 0


def validate_edits(proposal, before, root):
    if not isinstance(proposal, dict) or set(proposal) != {'diagnosis', 'edits', 'risk'}:
        raise RepairRejected('Fixer reply must contain diagnosis, edits and risk only')
    for key, limit in (('diagnosis', 600), ('risk', 300)):
        if not isinstance(proposal[key], str) or len(proposal[key]) > limit:
            raise RepairRejected(f'{key.capitalize()} exceeds its string limit')
        try:
            proposal[key].encode('utf-8')
        except UnicodeError:
            raise RepairRejected(f'{key.capitalize()} must be UTF-8 text') from None
    edits = proposal['edits']
    if not isinstance(edits, list) or not 1 <= len(edits) <= 5:
        raise RepairRejected('Fixer must propose 1 to 5 edits')
    after = before.copy()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {'path', 'find', 'replace'}:
            raise RepairRejected('Each edit must contain path, find and replace only')
        validate_path(edit['path'], before, root)
        find, replace = edit['find'], edit['replace']
        if not isinstance(find, str) or not find or not isinstance(replace, str):
            raise RepairRejected('Find must be non-empty text and replace must be text')
        if len(replace) > 4000:
            raise RepairRejected('Replacement exceeds 4,000 characters')
        text = after[edit['path']]
        if not unique_match(text, find):
            raise RepairRejected('Find must occur exactly once in its app file')
        after[edit['path']] = text.replace(find, replace, 1)
    try:
        size = sum(len(text.encode('utf-8')) for text in after.values())
    except UnicodeError:
        raise RepairRejected('Resulting file is not UTF-8') from None
    if size > SIZE_LIMIT:
        raise RepairRejected('Patched app exceeds the 2 MB size limit')
    return after


def save_files(root, files):
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode('utf-8'))


def diff_text(before, after):
    lines = []
    for name in sorted(before):
        # Keep unified diffs readable and valid for files without final newlines.
        for line in difflib.unified_diff(before[name].splitlines(keepends=True),
                after[name].splitlines(keepends=True), fromfile=name, tofile=name):
            lines.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    return ''.join(lines)


def changed_edits(before, after):
    """Turn CLI changes into bounded, uniquely anchored replacement spans."""
    edits = []
    for name, old in before.items():
        new = after[name]
        if old == new:
            continue
        start, end, new_end = 0, len(old), len(new)
        while start < min(end, new_end) and old[start] == new[start]:
            start += 1
        while end > start and new_end > start and old[end - 1] == new[new_end - 1]:
            end -= 1
            new_end -= 1
        while not unique_match(old, old[start:end]):
            if start:
                start -= 1
            elif end < len(old):
                end += 1
                new_end += 1
            else:
                raise RepairRejected('Muse Code edit has no unique existing text anchor')
        edits.append({'path': name, 'find': old[start:end], 'replace': new[start:new_end]})
    return edits


def write_report(out, record, diff):
    data = redact(record)
    diff = redact(diff)
    (out / 'repair.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (out / 'repair.diff').write_text(diff, encoding='utf-8')
    esc = lambda x: html.escape(str(x), quote=True)
    tone = {'FIXED': 'fixed', 'NOT_FIXED': 'failed'}.get(data['verdict'], 'other')
    colored = ''.join('<span class="' + ('added' if line.startswith('+') else
                       'removed' if line.startswith('-') else 'context') + '">' +
                       esc(line) + '</span>' for line in diff.splitlines(keepends=True))
    links = f'<a href="{esc(data["source_receipt_html"])}">Original FAIL receipt</a>'
    if data['replay_receipt']:
        links += f' · <a href="{esc(data["replay_receipt"])}">Replay receipt</a>'
    if data.get('fresh_check_receipt'):
        links += f' · <a href="{esc(data["fresh_check_receipt"])}">Fresh check: {esc(data["fresh_check_verdict"])}</a>'
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plainproof repair · {esc(data['verdict'])}</title><style>
*{{box-sizing:border-box}}body{{font:22px/1.45 system-ui,sans-serif;max-width:1220px;
margin:28px auto;padding:0 28px;color:#17283b;background:#f5f7fa}}
header,section{{margin-bottom:30px;padding:26px;background:white;border:1px solid #ccd6e0;border-radius:16px}}
h1{{font-size:60px;line-height:1.1;margin:0 0 16px}}h2{{font-size:34px}}
#verdict{{border-left:14px solid #ac7100;background:#fff3d6}}
#verdict.fixed{{border-color:#167345;background:#e5f5ed}}#verdict.failed{{border-color:#bb2438;background:#ffebee}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:18px}}a{{color:#125a91}}
.added{{color:#075431;background:#d3f4e2}}.removed{{color:#8e1426;background:#ffe0e5}}
</style><header id="verdict" class="{tone}"><p>Plainproof · repair receipt</p>
<h1>{esc(data['verdict'])}</h1><p>{esc(data.get('reason') or 'The recorded replay decides acceptance.')}</p></header>
<section><h2>Diagnosis</h2><p id="diagnosis">{esc(data['diagnosis'])}</p><p>{esc(data['risk'])}</p></section>
<section><h2>App diff</h2><pre id="diff">{colored or 'No edits applied.'}</pre></section>
<section id="links">{links}<p>Replay: {esc(data['replay_verdict'] or 'Not run')} · zero model calls</p>
<p>Fixer: {esc(data['fixer'])} · USD {esc(data['usd'])} · Seconds: {esc(data['seconds'])}</p></section></html>'''
    (out / 'repair.html').write_text(page, encoding='utf-8')
    return data


async def repair(receipt_dir, config, *, fixer='spark', fresh_check=False, on_event=None,
                 fixer_impl=None):
    source = Path(receipt_dir).resolve() / 'receipt.json'
    app = Path(config.app_dir).absolute() if config.app_dir else None
    output = Path(config.out).resolve()
    # Never even create output directories inside the original app or receipt.
    if app and (output == app.resolve() or app.resolve() in output.parents):
        raise ValueError('Repair output must be outside the original app directory')
    if output == source.parent or source.parent in output.parents:
        raise ValueError('Repair output must be outside the source receipt directory')
    out = prepare(output)
    started = time.monotonic()
    record = dict(format_version=1, verdict='ERROR', source_receipt=str(source),
        source_sha256=None, source_receipt_html=os.path.relpath(source.with_suffix('.html'), out),
        model=config.model, fixer=fixer, mock=config.mock, model_calls={'fixer': 0},
        usage={'fixer': empty_usage()}, usd=0, seconds=0, diagnosis='', edits=[], risk='',
        validation_results=[], replay_verdict=None, replay_receipt=None, replay_screenshot=None, reason=None)
    def emit(message):
        if on_event:
            on_event(redact({'type': 'status', 'message': message}))
    before, diff = None, ''
    mutated = False
    try:
        raw = source.read_bytes()
        record['source_sha256'] = hashlib.sha256(raw).hexdigest()
        original = json.loads(raw)
        if original.get('format_version') != 1 or original.get('verdict') != 'FAIL':
            raise RepairRejected('Repair requires a version 1 FAIL receipt')
        validate_plan(original['resolved_plan'])
        if app is None or config.app_url or config.model not in PRICES:
            raise RepairRejected('Repair requires an app directory and a supported model')
        emit('Copying and validating app files')
        before = snapshot(app)
        patched = out / 'patched_app'
        patched.mkdir()
        save_files(patched, before)
        record['validation_results'].append({'guard': 'app_copy', 'passed': True})
        failed = [r for r in original['check_results'] if r['passed'] is False]
        if not failed:
            raise RepairRejected('FAIL receipt has no failing checks')
        failed_shots = {r.get('screenshot') for r in failed}
        last_shot = next((cp['screenshot'] for cp in reversed(original['checkpoints'])
                          if cp['screenshot'] in failed_shots), None)
        if not isinstance(last_shot, str):
            raise RepairRejected('FAIL receipt has no failing checkpoint screenshot')
        shot = (source.parent / last_shot).resolve()
        if source.parent not in shot.parents or shot.suffix != '.png':
            raise RepairRejected('Failing screenshot must be inside the source receipt')
        context = dict(requirement=original['requirement'], resolved_plan=original['resolved_plan'],
            failing_checks=failed, executed_steps=[
                f"Step {i}: " + '; '.join(action_text(a) for a in s['executed'])
                for i, s in enumerate(original['steps'], 1)],
            app_files=[{'path': name, 'text': text} for name, text in before.items()])
        adapter = fixer_impl
        if adapter is None:
            if fixer == 'muse-code':
                if config.mock:
                    raise RepairRejected('--mock cannot launch Muse Code; use --fixer spark')
                adapter = MuseCodeFixer(enabled=True)
            elif fixer == 'spark':
                adapter = SparkFixer(config.model, config.mock)
            else:
                raise RepairRejected('Unknown fixer')
        emit('Proposing a minimal app repair')
        async with asyncio.timeout(config.timeout_s):
            mutated = bool(getattr(adapter, 'mutates_app', False))
            proposal = await adapter.propose(context, shot.read_bytes(), patched, out, record)
        if mutated:
            after = snapshot(patched)
            if set(after) != set(before):
                raise RepairRejected('Muse Code may only edit existing allowed app files')
            proposal['edits'] = changed_edits(before, after)
        # Validate the entire proposal in memory before applying a single edit.
        after = validate_edits(proposal, before, patched)
        if redact(proposal) != proposal:
            raise RepairRejected('Fixer reply contains a credential or image transport data')
        if source.read_bytes() != raw:
            raise RepairRejected('Source receipt changed during repair')
        if mutated:
            # The audited result must equal the result of the validated edits.
            if snapshot(patched) != after:
                raise RepairRejected('Muse Code files changed during validation')
        save_files(patched, after)
        diff = diff_text(before, after)
        record.update(diagnosis=proposal['diagnosis'], edits=proposal['edits'], risk=proposal['risk'])
        record['validation_results'].append({'guard': 'edits', 'passed': True})
        emit('Replaying the recorded actions and checks · zero model calls')
        result = await replay(source.parent, Config(out=str(out / 'replay'), app_dir=str(patched),
            max_steps=max(config.max_steps, len(original['steps'])), timeout_s=config.timeout_s))
        record.update(replay_verdict=result['verdict'], replay_receipt='replay/receipt.html',
                      verdict={'PASS': 'FIXED', 'FAIL': 'NOT_FIXED',
                               'INCONCLUSIVE': 'INCONCLUSIVE'}.get(result['verdict'], 'ERROR'))
        if result['checkpoints']:
            record['replay_screenshot'] = 'replay/' + result['checkpoints'][-1]['screenshot']
        if fresh_check and record['verdict'] == 'FIXED':
            emit('Running a separate fresh check on the patched app')
            try:
                fresh = await run(Config(out=str(out / 'fresh_check'), app_dir=str(patched),
                    requirement=original['requirement'], model=config.model, mock=config.mock,
                    approve_confirmations=config.approve_confirmations,
                    max_steps=config.max_steps, timeout_s=config.timeout_s))
                record.update(fresh_check_verdict=fresh['verdict'], fresh_check_receipt='fresh_check/receipt.html',
                              fresh_check_result=fresh)
            except Exception as exc:
                record.update(fresh_check_verdict='ERROR', fresh_check_result={'error': type(exc).__name__})
    except RepairRejected as exc:
        record.update(verdict='REPAIR_REJECTED', reason=str(exc))
        record['validation_results'].append({'guard': 'repair', 'passed': False, 'reason': str(exc)})
    except Exception as exc:
        # Exception bodies can include response data, credentials or image URLs.
        record.update(verdict='ERROR', reason=(str(exc) if isinstance(exc, RuntimeError) and
                      str(exc).startswith('Muse Code') else type(exc).__name__))
    finally:
        if mutated and before is not None and record['replay_verdict'] is None:
            # Discard all experimental CLI writes on rejection/error.
            import shutil
            patched = out / 'patched_app'
            if patched.is_symlink() or (patched.exists() and not patched.is_dir()):
                patched.unlink()
            elif patched.exists():
                shutil.rmtree(patched)
            patched.mkdir()
            save_files(patched, before)
        record['seconds'] = round(time.monotonic() - started, 3)
        record['usd'] = (cost(config.model, record['usage']['fixer'])
                         if record['usage'] and config.model in PRICES else None)
    data = write_report(out, record, diff)
    if on_event:
        on_event({'type': 'done', 'verdict': data['verdict'], 'receipt': 'repair.html',
                  'diff': redact(diff), 'diagnosis': data['diagnosis'],
                  'original_receipt': data['source_receipt_html'], 'replay_receipt': data['replay_receipt'],
                  'screenshot': data['replay_screenshot']})
    return data
