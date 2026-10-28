"""Assemble a 175-second demo with Pillow graphics and portable ffmpeg filters."""
import argparse
from collections import defaultdict
import math
from pathlib import Path
import subprocess
import wave
from PIL import Image, ImageDraw, ImageFont
from common import SCENES, SCENE_TIMES, TOTAL, CLOSING_START, duration, font_path, probe, read, write, coverage_gaps

W, H, FPS = 1920, 1080, 30
BG = (12, 22, 36)
BADGES = {'run_fail': 'Real Meta Model API run · muse-spark-1.3',
          'live_new': 'Real Meta Model API run · muse-spark-1.3',
          'repair': 'Real Meta Model API run · muse-spark-1.3',
          'guard_control': 'Offline negative control — operator does nothing'}


def ffmpeg(argv):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *map(str, argv)], check=True)


def wrap(draw, text, font, width):
    lines = ['']
    for word in text.split():
        trial = (lines[-1] + ' ' + word).strip()
        if draw.textlength(trial, font=font) > width and lines[-1]:
            lines.append(word)
        else:
            lines[-1] = trial
    return lines


def caption(path, text, font):
    im = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(im)
    for size in range(39, 23, -1):
        f = ImageFont.truetype(font, size)
        lines = wrap(d, text, f, 1740)
        if len(lines) <= 2:
            break
    if len(lines) > 2:
        raise ValueError('Caption exceeds two lines; split it in narration.json and regenerate TTS')
    d.rounded_rectangle((45, 898, W-45, 1062), radius=14, fill=(5, 12, 22, 238))
    for j, line in enumerate(lines):
        d.text((W/2, 940+j*49), line, font=f, fill='white', anchor='mm')
    im.save(path)


def badge(path, text, font, y=24):
    im = Image.new('RGBA', (W, H))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(font, 31)
    width = min(W-70, int(d.textlength(text, font=f))+44)
    d.rounded_rectangle((28, y, width+28, y+62), radius=12, fill=(10, 29, 44, 242))
    d.text((50, y+10), text, font=f, fill=(164, 244, 215))
    im.save(path)


def card(path, font, closing=False):
    im = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(im)
    def line(text, y, size=48, color='white'):
        d.text((W/2, y), text, font=ImageFont.truetype(font, size), fill=color, anchor='mm')
    if closing:
        line('Plainproof', 230, 86)
        line('Built during the Meta Global AI Developer Hackathon · Oct 27 – Nov 4, 2026', 430, 40)
        line('Verify · Repair · Re-verify — Muse Spark 1.3 + Meta Model API', 610, 43, '#a4f4d7')
    else:
        line('Plainproof', 220, 100)
        line('Proof before you accept an AI-built change', 365, 52)
        line('requirement → Muse Spark 1.3 planner', 550, 42, '#a4f4d7')
        line('→ Muse Spark 1.3 operator (computer tool)', 620, 42, '#a4f4d7')
        line('→ code judge → receipt → model-free replay', 690, 42, '#a4f4d7')
    im.save(path)


def watermark(path, font):
    im = Image.new('RGBA', (W, H))
    strip = Image.new('RGBA', (1800, 150))
    d = ImageDraw.Draw(strip)
    d.rounded_rectangle((0, 0, 1800, 150), radius=16, fill=(100, 0, 12, 155))
    d.text((900, 75), 'MOCK — NOT FOR SUBMISSION', font=ImageFont.truetype(font, 72), fill=(255, 210, 214, 235), anchor='mm')
    rotated = strip.rotate(23, expand=True, resample=Image.Resampling.BICUBIC)
    im.alpha_composite(rotated, ((W-rotated.width)//2, (H-rotated.height)//2))
    im.save(path)


def srt_time(seconds):
    ms = round(seconds*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--tts', type=Path, required=True)
    p.add_argument('--out', type=Path, default=Path('demo.mp4'))
    p.add_argument('--font')
    p.add_argument('--mock-watermark', action='store_true')
    args = p.parse_args()
    events, manifest = read(args.events), read(args.tts)
    if events.get('error'):
        p.error('Recording failed; inspect events.json before assembling')
    if (events['mock'] or manifest['fallback']) and not args.mock_watermark:
        p.error('Mock recording / fallback audio requires --mock-watermark')
    font = font_path(args.font)
    args.out = args.out.resolve()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    assets = args.out.parent / (args.out.stem + '_assets')
    assets.mkdir(exist_ok=True)
    sources = {}
    for name, source in events['raw_videos'].items():
        path = Path(source['path'])
        if not path.is_file():
            path = args.events.parent / path.name
        if not path.is_file():
            p.error('Raw video missing: ' + str(path))
        sources[name] = dict(path=path, offset=source['offset_seconds'], duration=duration(path))
    narration = defaultdict(list)
    phases = defaultdict(list)
    for c in manifest['clips']:
        c = dict(c)
        c['path'] = str((args.tts.parent / c['path']).resolve())
        c['duration'] = duration(c['path'])
        narration[c['scene']].append(c)
    for ph in events['phases']:
        if ph['end'] <= ph['start'] or ph['start'] < 0:
            p.error('Invalid event interval')
        phases[ph['scene']].append(ph)
    if set(phases) != set(SCENES) or set(narration) != set(SCENES):
        p.error('All six scenes need footage and narration')
    for ph in events['phases']:
        source = sources[ph['video']]
        if ph['start'] < source['offset'] or ph['end']-source['offset'] > source['duration']+.15:
            p.error('Events extend past their raw recording')

    # Fixed scene boundaries are shared with DEMO_SCRIPT.md and the verifier.
    # Only waits may accelerate; all typing and evidence remain at normal speed.
    frames = {scene: round((end-start)*FPS) for scene,(start,end) in SCENE_TIMES.items()}
    for scene in SCENES:
        fixed = sum(ph['end']-ph['start'] for ph in phases[scene] if not ph['waiting'])
        wait = sum(ph['end']-ph['start'] for ph in phases[scene] if ph['waiting'])
        target = frames[scene]/FPS
        if fixed + min(wait, 1) > target:
            p.error('Normal-speed footage exceeds the planned ' + scene + ' timing; record shorter inspection pauses')
        speech = sum(c['duration'] for c in narration[scene])
        speech_window = target + (4 if scene == 'intro' else 0)
        if speech > speech_window:
            p.error('Narration exceeds the planned ' + scene + ' timing; increase the TTS rate')
        if (speech_window-speech)/len(narration[scene]) > 4:
            p.error('Narration leaves gaps over 4s in ' + scene + '; lower the TTS rate')
    segments, captions, scene_times = [], [], []
    cursor = 4.0
    for s in SCENES:
        cursor, end = SCENE_TIMES[s]
        target = end-cursor
        fixed = sum(ph['end']-ph['start'] for ph in phases[s] if not ph['waiting'])
        wait = sum(ph['end']-ph['start'] for ph in phases[s] if ph['waiting'])
        factor = max(1., wait/max(.05, target-fixed)) if wait else 1.
        parts = []
        local = 0.
        for ph in phases[s]:
            speed = factor if ph['waiting'] else 1.
            length = (ph['end']-ph['start'])/speed
            part = dict(scene=s, phase=ph['phase'], start=cursor+local, end=cursor+local+length,
                        raw_start=ph['start']-sources[ph['video']]['offset'],
                        raw_end=ph['end']-sources[ph['video']]['offset'],
                        raw_video=str(sources[ph['video']]['path']), video=ph['video'], speed_factor=speed,
                        waiting=ph['waiting'], hold=False)
            parts.append(part)
            local += length
        if target-local > .01:
            parts.append(dict(scene=s, phase='hold_final_frame', start=cursor+local,
                end=cursor+target, raw_start=phases[s][-1]['end']-.08-sources[phases[s][-1]['video']]['offset'],
                raw_end=phases[s][-1]['end']-.08-sources[phases[s][-1]['video']]['offset'],
                raw_video=str(sources[phases[s][-1]['video']]['path']), video=phases[s][-1]['video'],
                speed_factor=1., waiting=False, hold=True))
        # Spread pauses through the scene instead of leaving a long silent tail.
        # Intro speech begins over the title. Pauses stay distributed, with
        # the final half-gap checked together with the silent closing card.
        speech_start = 0 if s == 'intro' else cursor
        speech_end = cursor + target
        slack = speech_end-speech_start-sum(c['duration'] for c in narration[s])
        gap = slack / len(narration[s])
        speech_cursor = speech_start + gap/2
        for c in narration[s]:
            captions.append(dict(scene=s, text=c['text'], start=speech_cursor,
                                  end=speech_cursor+c['duration'], path=c['path']))
            speech_cursor += c['duration'] + gap
        segments.extend(parts)
        scene_times.append(dict(scene=s, start=cursor, end=cursor+target))
        cursor += target
    focus = []
    for event in events.get('focus', []):
        part = next((seg for seg in segments if seg['scene']==event['scene'] and
                     seg['phase']==event['phase'] and not seg['hold']), None)
        if not part:
            p.error('Focus has no matching footage segment')
        start = part['start'] + (event['start']-sources[event['video']]['offset']-part['raw_start'])/part['speed_factor']
        end = part['start'] + (event['end']-sources[event['video']]['offset']-part['raw_start'])/part['speed_factor']
        held = next((seg for seg in segments if seg['scene']==event['scene'] and seg['hold'] and
                     event['start']-sources[event['video']]['offset'] <= seg['raw_start'] <= event['end']-sources[event['video']]['offset']), None)
        if held:
            end = held['end']
        rect = event['rect']
        # The unchanged wide view fits 1280x900 footage into a 1920x1080
        # canvas at (320,90). Grow its measured rectangle to 16:9, never shrink.
        width = math.ceil(max(rect['width'], rect['height']*16/9)/32)*32
        height = width*9/16
        cx = rect['x']+rect['width']/2+320
        cy = rect['y']+rect['height']/2+90
        width = min(W, width)
        height = width*9/16
        x = min(max(0, cx-width/2), W-width)
        y = min(max(0, cy-height/2), H-height)
        focus.append(dict(scene=event['scene'], phase=event['phase'], start=start, end=end,
            viewport_rect=rect, crop=dict(x=x, y=y, width=width, height=height),
            transition_seconds=.4, transition_steps=6, held_final_frame=bool(held)))
    if not focus:
        p.error('No recorded focus intervals; re-record with the updated recorder')
    gaps = coverage_gaps(captions, TOTAL)
    if any(min(g['end'], CLOSING_START)-g['start'] > 4.0001 for g in gaps if g['start'] < CLOSING_START):
        p.error('Planned narration contains a silence longer than 4s before the closing card')
    control = events['receipts'][events['receipt_ids']['guard_control']]
    evidence_badges = []
    for scene, phase, text in (
            ('repair', 'replay_verdict', 'Repair acceptance = replay · zero model calls'),
            ('guard_control', 'verdict_reason', control['verdict'] + ' · ' + control['verdict_reason'])):
        evidence_badges.append(dict(scene=scene, text=text,
            start=next(part['start'] for part in segments if part['scene']==scene and part['phase']==phase),
            end=SCENE_TIMES[scene][1]))
    write(args.out.with_name('timeline.json'), dict(duration=TOTAL, scenes=scene_times,
          segments=segments, captions=captions, focus=focus, title=dict(start=0,end=4),
          closing_card=dict(start=CLOSING_START,end=TOTAL), mock=args.mock_watermark,
          narration_fallback=manifest['fallback'], evidence_badges=evidence_badges))
    args.out.with_suffix('.srt').write_text('\n\n'.join(
        f'{i+1}\n{srt_time(c["start"])} --> {srt_time(c["end"])}\n{c["text"]}' for i,c in enumerate(captions))+'\n', encoding='utf-8')
    for i,c in enumerate(captions):
        caption(assets / f'caption-{i}.png', c['text'], font)
    if args.mock_watermark:
        watermark(assets / 'watermark.png', font)
    for s,label in BADGES.items():
        # Mock footage must never carry an unqualified claim of a real API run.
        if events['mock'] and s in {'run_fail', 'repair', 'live_new'}:
            label = 'MOCK offline run · muse-spark-1.3 scenario'
        badge(assets / f'{s}-badge.png', label, font)
    for i, item in enumerate(evidence_badges):
        badge(assets / f'evidence-{i}.png', item['text'], font, y=100)

    outputs = []
    def render(name, parts, overlays, length, camera=()):
        argv, graph, labels = [], [], []
        index = 0
        for part in parts:
            if part.get('card'):
                argv += ['-loop', '1', '-framerate', '30', '-t', length, '-i', part['card']]
            elif part['hold']:
                still = assets / f'{name}-hold.png'
                ffmpeg(['-ss', part['raw_start'], '-i', part['raw_video'], '-frames:v', '1', still])
                argv += ['-loop', '1', '-framerate', '30', '-t', part['end']-part['start'], '-i', still]
            else:
                argv += ['-threads', '1', '-ss', part['raw_start'], '-t', part['raw_end']-part['raw_start'], '-i', part['raw_video']]
            speed = part.get('speed_factor', 1)
            content_height = H if part.get('card') else 900
            graph.append(f'[{index}:v]setpts=(PTS-STARTPTS)/{speed:.9f},scale={W}:{content_height}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0c1624[v{index}]')
            labels.append(f'[v{index}]')
            index += 1
        graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0[base]')
        last='base'
        if camera:
            # First produce a uniform 30fps canvas. Then crop exact frame ranges
            # with fixed rectangles: crop's width/height and input bounds are
            # static on some ffmpeg builds, so dynamic scale + crop can miscenter.
            canvas = assets / f'{name}-canvas.mp4'
            ffmpeg([*argv, '-filter_complex_threads', '1', '-filter_complex', ';'.join(graph),
                '-map', '[base]', '-an', '-t', length, '-r', FPS, '-c:v', 'libx264',
                '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-threads', '2', canvas])
            count = round(length*FPS)
            cuts = {0, count}
            for f in camera:
                a, b = f['start'], f['end']
                ramp = min(f['transition_seconds'], (b-a)/2)
                for step in range(7):
                    for moment in (a+ramp*step/6, b-ramp*step/6):
                        cuts.add(min(count, max(0, round(moment*FPS))))
            cuts = sorted(cuts)
            argv, graph, labels = [], [], []
            for index,(first,last_frame) in enumerate(zip(cuts, cuts[1:])):
                t = (first+last_frame)/(2*FPS)
                active = next((f for f in camera if f['start'] <= t <= f['end']), None)
                x, y, width, height = 0, 0, W, H
                if active:
                    a, b = active['start'], active['end']
                    ramp = min(active['transition_seconds'], (b-a)/2)
                    progress = min(1., (t-a)/ramp, (b-t)/ramp)
                    ease = (1-math.cos(math.pi*progress))/2
                    r = active['crop']
                    zoom = 1+(W/r['width']-1)*ease
                    cx = W/2+(r['x']+r['width']/2-W/2)*ease
                    cy = H/2+(r['y']+r['height']/2-H/2)*ease
                    # Multiples of 32x18 keep an exact 16:9 aspect and square
                    # pixels across concat, without needing a setsar filter.
                    units = math.ceil(W/zoom/32)
                    width, height = units*32, units*18
                    x = max(0, min(W-width, round(cx-width/2)))
                    y = max(0, min(H-height, round(cy-height/2)))
                argv += ['-threads', '1', '-ss', first/FPS, '-t', (last_frame-first)/FPS, '-i', canvas]
                graph.append(f'[{index}:v]setpts=PTS-STARTPTS,crop={width}:{height}:{x}:{y},scale={W}:{H}[shot{index}]')
                labels.append(f'[shot{index}]')
            graph.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0[camera]')
            index = len(labels)
            last='camera'
        if args.mock_watermark:
            overlays.append((assets / 'watermark.png', 0, length))
        for j,(png,start,end) in enumerate(overlays):
            argv += ['-i', png]
            next_label=f'overlay{j}'
            graph.append(f"[{last}][{index}:v]overlay=0:0:enable='between(t,{start:.6f},{end:.6f})'[{next_label}]")
            last=next_label
            index += 1
        output = assets / f'{name}.mp4'
        ffmpeg([*argv, '-filter_complex_threads', '1', '-filter_complex', ';'.join(graph),
            '-map', f'[{last}]', '-an', '-t', length, '-r', FPS, '-c:v', 'libx264',
            '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p', '-threads', '2', output])
        outputs.append(output)
        print(f'Rendered {name}: {length:.2f}s', flush=True)

    card(assets / 'title.png', font)
    render('00-title', [dict(card=assets / 'title.png')],
           [(assets / f'caption-{j}.png', c['start'], min(4, c['end']))
            for j,c in enumerate(captions) if c['start'] < 4], 4)
    for i,scene in enumerate(scene_times):
        s, start = scene['scene'], scene['start']
        length = scene['end']-start
        overlays = [(assets / f'caption-{j}.png', c['start']-start, c['end']-start)
                    for j,c in enumerate(captions) if c['scene']==s]
        overlays += [(assets / f'evidence-{j}.png', item['start']-start, item['end']-start)
                     for j,item in enumerate(evidence_badges) if item['scene']==s]
        if s in BADGES:
            overlays.append((assets / f'{s}-badge.png', 0, length))
        parts = [part for part in segments if part['scene']==s]
        for j,part in enumerate(parts):
            if part['speed_factor'] > 1.01:
                png = assets / f'{s}-waiting-{j}.png'
                badge(png, f'Waiting accelerated ×{max(1, round(part["speed_factor"]))}', font, y=100)
                overlays.append((png, part['start']-start, part['end']-start))
        camera = [{**f, 'start': f['start']-start, 'end': f['end']-start} for f in focus if f['scene']==s]
        render(f'{i+1:02d}-{s}', parts, overlays, length, camera)
    card(assets / 'closing.png', font, closing=True)
    render('07-closing-card', [dict(card=assets / 'closing.png')], [], 6)
    # Identical encodes allow lossless packet concatenation. This is the concat
    # demuxer, not another video filter.
    listing = assets / 'concat.txt'
    listing.write_text(''.join("file '"+str(path).replace("'", "'\\''")+"'\n" for path in outputs))
    silent = assets / 'picture.mp4'
    ffmpeg(['-f', 'concat', '-safe', '0', '-i', listing, '-c', 'copy', silent])
    # PCM placement avoids huge filter graphs and sums overlaps in integer PCM.
    # Clips are scheduled without overlap, preserving exact audio amplitude.
    pcm = bytearray(TOTAL*48000*2)
    previous_end = 0
    for c in captions:
        with wave.open(c['path'], 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1,2,48000):
                raise ValueError('TTS must be mono signed 16-bit 48kHz WAV')
            data = wav.readframes(wav.getnframes())
        offset = round(c['start']*48000)*2
        if offset < previous_end or offset+len(data)>len(pcm):
            raise ValueError('Narration overlaps or extends past output')
        pcm[offset:offset+len(data)] = data
        previous_end = offset+len(data)
    audio = assets / 'narration.wav'
    with wave.open(str(audio), 'wb') as wav:
        wav.setparams((1,2,48000,0,'NONE','not compressed'))
        wav.writeframes(pcm)
    ffmpeg(['-i', silent, '-i', audio, '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k', '-t', TOTAL, '-movflags', '+faststart', args.out])
    info = probe(args.out)
    video = next(s for s in info['streams'] if s['codec_type']=='video')
    if (video['width'],video['height']) != (W,H) or video['avg_frame_rate'] != '30/1' or not any(s['codec_type']=='audio' for s in info['streams']) or not 170 <= float(info['format']['duration']) <= 180:
        raise RuntimeError('Final ffprobe validation failed')
    print(f'{args.out}: {info["format"]["duration"]}s, 1920x1080, 30 fps, H.264 + AAC')


if __name__ == '__main__':
    main()
