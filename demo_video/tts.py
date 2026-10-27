"""Create narration WAVs using macOS say, or an explicitly requested test fallback."""
import argparse
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from common import HERE, duration, read, write


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--narration', type=Path, default=HERE / 'narration.json')
    p.add_argument('--out', type=Path, default=HERE / 'tts')
    p.add_argument('--voice', default='Ava (Premium)')
    p.add_argument('--rate', type=int, default=170)
    p.add_argument('--fallback', action='store_true')
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, line in enumerate(read(args.narration)):
        wav = args.out / f'{i:02d}-{line["scene"]}.wav'
        source = wav.with_suffix('.aiff' if not args.fallback else '.source.wav')
        if not args.fallback:
            if sys.platform != 'darwin':
                p.error('macOS say required; use --fallback for Linux tests')
            subprocess.run(['say', '-v', args.voice, '-r', str(args.rate), '-o', str(source), line['text']], check=True)
            engine = 'say'
        elif shutil.which('espeak-ng'):
            subprocess.run(['espeak-ng', '-s', str(args.rate), '-w', str(source), line['text']], check=True)
            engine = 'espeak-ng'
        else:
            seconds = len(line['text'].split()) / 2.6
            with wave.open(str(source), 'wb') as out:
                out.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\0\0' * round(seconds * 48000))
            engine = 'silence'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-ar', '48000',
                        '-ac', '1', '-c:a', 'pcm_s16le', str(wav)], check=True)
        source.unlink()
        clips.append(dict(line, path=wav.resolve().as_posix(), duration=duration(wav), engine=engine))
    write(args.out / 'manifest.json', {'sample_rate': 48000, 'fallback': args.fallback, 'clips': clips})
    print(f'{len(clips)} clips, {sum(c["duration"] for c in clips):.2f}s; {args.out / "manifest.json"}')


if __name__ == '__main__':
    main()
