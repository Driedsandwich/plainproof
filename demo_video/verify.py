"""Verify the demo, narration gaps and focus intervals; save six zoomed frames."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
from PIL import Image, ImageStat
from common import HERE, SCENES, probe, read, write, coverage_gaps


def measured_silence(video, end):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', str(video),
        '-af', 'silencedetect=noise=-45dB:d=0.05', '-f', 'null', '-'],
        capture_output=True, text=True, check=True)
    gaps, start = [], None
    for line in result.stderr.splitlines():
        begin = re.search(r'silence_start: ([\d.e+-]+)', line)
        stop = re.search(r'silence_end: ([\d.e+-]+)', line)
        if begin:
            start = max(0., float(begin[1]))
        if stop and start is not None:
            finish = min(end, float(stop[1]))
            gaps.append(dict(start=start, end=finish, duration=finish-start))
            start = None
    if start is not None:
        gaps.append(dict(start=start, end=end, duration=end-start))
    return gaps


def check_gaps(gaps, closing_start):
    # Count only the part of each gap before the closing card; the card itself is meant to be silent.
    longest = max((min(g['end'], closing_start) - g['start'] for g in gaps if g['start'] < closing_start - .001),
                  default=0.)
    assert longest <= 4.0001, f'Silence before closing card is {longest:.3f}s (maximum 4.0s)'
    return longest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--timeline', type=Path, required=True)
    p.add_argument('--frames', type=Path, default=HERE / 'test_frames')
    p.add_argument('--report', type=Path)
    p.add_argument('--expect-narration-timeline', action='store_true',
                   help='Validate planned caption coverage instead of silent fallback audio')
    args = p.parse_args()
    args.frames.mkdir(parents=True, exist_ok=True)
    info = probe(args.video)
    video = next(s for s in info['streams'] if s['codec_type']=='video')
    audio = next(s for s in info['streams'] if s['codec_type']=='audio')
    seconds = float(info['format']['duration'])
    assert (video['width'],video['height']) == (1920,1080)
    assert video['avg_frame_rate'] == '30/1' and video['codec_name']=='h264'
    assert audio['codec_name']=='aac' and int(audio['sample_rate'])==48000
    assert 170 <= seconds <= 180
    timeline = read(args.timeline)
    assert [s['scene'] for s in timeline['scenes']]==list(SCENES)
    assert abs(timeline['duration']-seconds) < .1
    assert abs(timeline['closing_card']['end']-timeline['closing_card']['start']-6) < .001
    for segment in timeline['segments']:
        if not segment['waiting']:
            assert segment['speed_factor']==1
    for c in timeline['captions']:
        assert 0 <= c['start'] < c['end'] <= timeline['closing_card']['start']+.001
    # Additions may appear between original lines, but every original word must
    # remain verbatim and in order. The rendered timeline must match narration.json.
    script = (HERE.parent / 'DEMO_SCRIPT.md').read_text()
    expected = ' '.join(line.split('|')[-2].strip()[1:-1]
        for line in script.splitlines() if re.match(r'\| [012]:\d\d', line))
    lines = read(HERE / 'narration.json')
    cursor = 0
    original = []
    for line in lines:
        text = line['text']
        if expected.startswith(text, cursor):
            original.append(text)
            cursor += len(text)
            if cursor < len(expected) and expected[cursor]==' ':
                cursor += 1
    assert ' '.join(original) == expected, 'An original narration line changed or is missing'
    assert [(c['scene'],c['text']) for c in timeline['captions']] == [(c['scene'],c['text']) for c in lines]
    focus = timeline.get('focus', [])
    required = {('receipt_fail','verdict'), ('receipt_fail','evidence'),
        ('replay_receipt','verdict'), ('replay_receipt','zero_model_calls'), ('replay_receipt','zero_usd'),
        ('live_new','marker_seen'), ('live_new','final_checks'), ('guard_control','verdict_reason')}
    assert required <= {(f['scene'], f['phase']) for f in focus}, 'Missing focus intervals'
    previous = 0.
    for f in focus:
        assert previous <= f['start'] < f['end'] <= seconds
        previous = f['end']
        rect, crop = f['viewport_rect'], f['crop']
        assert 0 <= rect['x'] < 1280 and 0 <= rect['y'] < 900
        assert 0 < rect['width'] <= 1280-rect['x']+.01 and 0 < rect['height'] <= 900-rect['y']+.01
        assert abs(crop['width']/crop['height']-16/9) < .001
        assert 0 < crop['width'] < 1920 and 0 <= crop['x'] <= 1920-crop['width']+.01
        assert 0 <= crop['y'] <= 1080-crop['height']+.01
        assert .3 <= f['transition_seconds'] <= .5
        source = next(s for s in timeline['segments'] if s['scene']==f['scene'] and s['phase']==f['phase'])
        assert source['start'] <= f['start']+.001
        if f.get('held_final_frame'):
            hold = next(s for s in timeline['segments'] if s['scene']==f['scene'] and s['hold'])
            assert source['raw_start'] <= hold['raw_start'] <= source['raw_end']
            assert abs(f['end']-hold['end']) < .001
        else:
            assert source['end'] >= f['end']-.001
        assert f['transition_steps'] == 6
    measured = measured_silence(args.video, seconds)
    planned = coverage_gaps(timeline['captions'], seconds)
    closing = timeline['closing_card']['start']
    if args.expect_narration_timeline:
        assert timeline.get('narration_fallback'), 'Timeline mode is only for fallback rehearsals'
    chosen = planned if args.expect_narration_timeline else measured
    longest = check_gaps(chosen, closing)
    selections = [('receipt_fail','verdict'), ('receipt_fail','evidence'),
                  ('replay_receipt','run_details'), ('live_new','marker_seen'),
                  ('live_new','final_checks'), ('guard_control','verdict_reason')]
    frames = []

    def inspect(path, timestamp):
        subprocess.run(['ffmpeg','-v','error','-y','-ss',str(timestamp),'-i',str(args.video),
                        '-frames:v','1',str(path)], check=True)
        with Image.open(path) as im:
            gray = im.convert('L')
            stddev, entropy = ImageStat.Stat(gray).stddev[0], gray.entropy()
            # Zoomed receipt frames are mostly white text pages (low entropy, real contrast); a blank frame has stddev near 0.
            assert stddev>12 and entropy>0.5, (path,stddev,entropy)
        return dict(seconds=timestamp,stddev=round(stddev,3),entropy=round(entropy,3))

    # Still inspect every scene, including those without receipt focus targets.
    with tempfile.TemporaryDirectory() as tmp:
        for scene in timeline['scenes']:
            inspect(Path(tmp)/'scene.png', (scene['start']+scene['end'])/2)
    for i,(scene,phase) in enumerate(selections, 1):
        f = next(f for f in focus if f['scene']==scene and f['phase']==phase)
        path = args.frames / f'{i:02d}-{scene}-{phase}.png'
        frames.append(dict(scene=scene,phase=phase,focus=True,saved=str(path.resolve()),
                           **inspect(path, (f['start']+f['end'])/2)))
    report = dict(duration_seconds=seconds,width=video['width'],height=video['height'],
        fps=video['avg_frame_rate'],video_codec=video['codec_name'],audio_codec=audio['codec_name'],
        audio_sample_rate=int(audio['sample_rate']),bytes=int(info['format']['size']),
        mock_watermark=timeline['mock'],verbatim_original_narration=True,normal_speed_typing_results=True,
        silence_mode='planned narration timing' if args.expect_narration_timeline else 'measured audio (-45 dB)',
        longest_gap_seconds=longest, longest_gap_including_closing_seconds=max((g['duration'] for g in chosen),default=0),
        measured_longest_gap_seconds=max((g['duration'] for g in measured),default=0),
        silence_gaps=chosen, focus_intervals=len(focus), frames=frames)
    output = args.report or args.video.with_name('verification.json')
    write(output, report)
    print(f'{seconds:.3f}s; longest pre-closing gap {longest:.3f}s ({report["silence_mode"]}); '
          f'{len(focus)} focus intervals; eight nonblank scenes; six zoomed frames')
    print(output)


if __name__=='__main__':
    main()
