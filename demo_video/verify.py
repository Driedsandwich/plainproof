"""Check the rendered demo, verify captions, and save six review frames."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
from PIL import Image, ImageStat
from common import HERE, SCENES, probe, read, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--timeline', type=Path, required=True)
    p.add_argument('--frames', type=Path, default=HERE / 'test_frames')
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    args.frames.mkdir(parents=True, exist_ok=True)
    info = probe(args.video)
    video = next(s for s in info['streams'] if s['codec_type']=='video')
    audio = next(s for s in info['streams'] if s['codec_type']=='audio')
    assert (video['width'],video['height']) == (1920,1080)
    assert video['avg_frame_rate'] == '30/1' and video['codec_name']=='h264'
    assert audio['codec_name']=='aac' and int(audio['sample_rate'])==48000
    assert 170 <= float(info['format']['duration']) <= 180
    timeline = read(args.timeline)
    assert [s['scene'] for s in timeline['scenes']]==list(SCENES)
    for segment in timeline['segments']:
        if not segment['waiting']:
            assert segment['speed_factor']==1
    for c in timeline['captions']:
        assert 4 <= c['start'] < c['end'] <= 169
    # Verbatim comparison is independent of how long paragraphs were split.
    script = (HERE.parent / 'DEMO_SCRIPT.md').read_text()
    expected = ' '.join(line.split('|')[-2].strip()[1:-1]
        for line in script.splitlines() if re.match(r'\| [012]:\d\d', line))
    actual = ' '.join(c['text'] for c in read(HERE / 'narration.json'))
    assert actual == expected, 'Narration differs from DEMO_SCRIPT.md'
    assert ' '.join(c['text'] for c in timeline['captions']) == expected
    preferred = dict(intro='ui', run_fail='result', receipt_fail='evidence', replay='terminal',
        replay_receipt='counter_evidence', live_new='marker_seen', guard_control='passing_checks', closing='repository')
    frames=[]
    for scene in SCENES:
        segment = next(s for s in timeline['segments'] if s['scene']==scene and s['phase']==preferred[scene])
        timestamp = (segment['start']+segment['end'])/2
        # Inspect every scene. Keep the six action/evidence frames requested;
        # intro and closing are checked in temporary files as well.
        keep = scene not in {'intro','closing'}
        with tempfile.TemporaryDirectory() as tmp:
            path = args.frames / f'{scene}.png' if keep else Path(tmp) / f'{scene}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(timestamp),'-i',str(args.video),
                            '-frames:v','1',str(path)],check=True)
            with Image.open(path) as im:
                gray=im.convert('L')
                stddev=ImageStat.Stat(gray).stddev[0]
                entropy=gray.entropy()
                assert stddev>12 and entropy>2, (scene,stddev,entropy)
            frames.append(dict(scene=scene,seconds=timestamp,stddev=round(stddev,3),entropy=round(entropy,3),
                               saved=str(path.resolve()) if keep else None))
    report=dict(duration_seconds=float(info['format']['duration']),width=video['width'],height=video['height'],
        fps=video['avg_frame_rate'],video_codec=video['codec_name'],audio_codec=audio['codec_name'],
        audio_sample_rate=int(audio['sample_rate']),bytes=int(info['format']['size']),
        mock_watermark=timeline['mock'],verbatim_narration=True,normal_speed_typing_results=True,frames=frames)
    output=args.report or args.video.with_name('verification.json')
    write(output,report)
    print(f'{report["duration_seconds"]:.3f}s; 1920x1080; 30fps; H.264 + AAC 48kHz; eight nonblank scenes; six saved frames')
    print(output)


if __name__=='__main__':
    main()
