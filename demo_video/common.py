"""Small helpers shared by the demo tools; no server settings or secrets."""
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Shared by the script, assembler and verifier. Title precedes intro footage.
SCENE_TIMES = {'intro': (4, 20), 'run_fail': (20, 53),
               'receipt_fail': (53, 78), 'repair': (78, 120),
               'live_new': (120, 148), 'guard_control': (148, 169)}
SCENES = tuple(SCENE_TIMES)
TOTAL = 175
CLOSING_START = 169
FONTS = ('/System/Library/Fonts/Helvetica.ttc', '/System/Library/Fonts/SFNS.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def duration(path):
    return float(probe(path)['format']['duration'])


def font_path(explicit=None):
    if explicit:
        if not Path(explicit).is_file():
            raise ValueError('Font does not exist: ' + explicit)
        return explicit
    for name in FONTS:
        if Path(name).is_file():
            return name
    raise ValueError('No default font found; pass --font /path/to/font.ttf')


def coverage_gaps(intervals, end):
    """Return uncovered time, including leading and trailing silence."""
    cursor, gaps = 0., []
    for item in sorted(intervals, key=lambda item: item['start']):
        start, stop = max(0., item['start']), min(end, item['end'])
        if start > cursor:
            gaps.append(dict(start=cursor, end=start, duration=start-cursor))
        cursor = max(cursor, stop)
    if cursor < end:
        gaps.append(dict(start=cursor, end=end, duration=end-cursor))
    return gaps
