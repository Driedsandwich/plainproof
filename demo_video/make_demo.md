# Record Plainproof on the Mac

Run commands from the repository root. The server is the only process that
needs the Meta credential. Use a separate terminal that has no credential set
for all demo tools. These scripts never load `.env`, inspect environment values,
request a key, or pass a key to a subprocess. Do not print environment variables.
Only the explicitly offline no-op control runs as a CLI subprocess from that terminal.

## Install and check

Activate your Python 3.15 virtual environment, then:

```sh
python -m pip install -r requirements.txt -r requirements-demo.txt
python -m playwright install chromium --only-shell
ffmpeg -version
ffprobe -version
say -v 'Ava (Premium)' -r 170 -o /tmp/plainproof-voice-check.aiff 'Plainproof'
```

Pillow 13, Playwright 1.64.1, `openai`, `ffmpeg`, and `ffprobe` must be available.
Listen to the voice check. Install the Ava Premium voice in macOS if needed.
No libass, drawtext, FreeType, or subtitle support is needed in ffmpeg. All text
is rendered with Pillow. The only video filters used are scale, crop, pad, setpts,
concat, and overlay; verification also uses silencedetect; audio is assembled as padded PCM using Python's wave module.

Use a font with Latin punctuation and arrows. `--font /path/to/font.ttf` overrides
this default search order: `/System/Library/Fonts/Helvetica.ttc`,
`/System/Library/Fonts/SFNS.ttf`, `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`.
Font size is fitted to at most two caption lines; overly long captions fail with
an instruction to split the narration rather than silently truncate it.

## Start the server in its own terminal

In the server terminal, activate the same venv and arrange the credential in
that terminal's environment privately using your existing workflow. Do this off
camera. The following command does not set, print, or copy the credential:

```sh
python -m plainproof serve --port 0 --timeout-s 420 --out runs/demo-real
```

Port `0` asks the OS for a free loopback port. Copy the printed URL, for example
`http://127.0.0.1:54321`. Use the updated `web/server.py`: it serves completed
CLI receipts inside its output directory as well as web jobs. The output path
must be the same one supplied to `record.py --web-out`. Keep the server running.
Use a fresh server output directory for each recording, because `noop-control`
is a fixed directory name and CLI receipts are never overwritten.

## Record in a separate credential-free terminal

Activate the venv in this terminal, but do not set a credential or source any
credential-bearing configuration. Replace only the URL below with the printed
server URL:

```sh
python demo_video/record.py \
  --base http://127.0.0.1:54321 \
  --web-out runs/demo-real \
  --repo . \
  --out demo_video/recordings/real-take-01
```

Do not pass `--mock` for the real take. The two proof runs and repair use
muse-spark-1.3. The proof runs clear Offline demo and enable local confirmation
approval; repair inherits that run’s mode and source app.
The server handles the credential independently. The recorder waits up to seven
minutes for each receipt; `serve --timeout-s 420` sets the web run budget to
match. The recorder does not retry paid runs automatically.

The recorder keeps a headless 1280×900 UI tab alive and records receipts in a
second tab. It types at 35 ms per key. After the FAIL receipt inspection it
returns to the retained UI, checks that the exact FAIL receipt is still selected,
clicks `#repair`, and waits up to three minutes for the repair result. It focuses
`#repair-diagnosis`, the colored `#repair-diff`, and `#repair-banner`, then clicks
`#repair-replay` in the repaired UI tab and focuses PASS and Run details, including
zero model calls and USD 0. This acceptance replay replaces the old CLI replay.
The repair uses the original failing app and a throwaway patched copy.

Verdicts and usage are read from the server’s actual `repair.json` and
`replay/receipt.json`. Repair must be FIXED; replay must be PASS with zero planner
and operator calls and USD 0. The source must match the filmed FAIL receipt.
Unexpected results stop recording and retain both raw tab takes plus an error in
`events.json`; review the honest result and use fresh output directories for a
new attempt. The recorder never retries paid runs automatically.

After repair, the recorder types the new delete requirement into Correct and
inspects its PASS and positive marker sighting. It creates the offline no-op
control using `python -m plainproof check --mock --mock-scenario noop`.
This scenario matches `tests.run_tests.NoOpOperator`: one wait action, then a
completion claim. INCONCLUSIVE intentionally exits 2. The recorder requires
marker_never_seen and passing final checks.

Each tab’s uncut WebM is retained. Two pre-roll swatches independently calibrate
its encoded clock (about 0.12 s precision). `events.json` identifies each phase’s
source video; assembly excludes the swatches and joins the actual tab footage.
No predicted terminal output or receipt content is substituted.

## Narrate and assemble

```sh
python demo_video/tts.py
python demo_video/assemble.py \
  --events demo_video/recordings/real-take-01/events.json \
  --tts demo_video/tts/manifest.json \
  --out demo_video/demo.mp4
python demo_video/verify.py \
  --video demo_video/demo.mp4 \
  --timeline demo_video/timeline.json \
  --frames demo_video/real_frames
```

Default voice: `say -v 'Ava (Premium)' -r 170`. WAV clips are mono 48 kHz,
16-bit PCM. Manifest durations are measured by ffprobe. Use `--font` on the
assembler if necessary. Output is 175 seconds, 1920×1080, 30 fps, H.264/AAC;
`demo.srt` and `timeline.json` sit beside the video. Title is 4 seconds and final
card is 6 seconds. Scene boundaries exactly match DEMO_SCRIPT.md: intro 0:00–0:20 (including
the title), counter run 0:20–0:53, FAIL receipt 0:53–1:18, repair 1:18–2:00,
delete 2:00–2:28, control 2:28–2:49, closing card 2:49–2:55. Only waiting is
accelerated, with a visible rounded factor badge; spare time holds the final
scene frame. If narration or normal-speed footage cannot fit, assembly fails
rather than speeding typing/results or truncating speech. Increase TTS rate
if speech is too long, lower it if narration gaps exceed four seconds, or
re-record shorter inspection pauses if footage is too long; then review.

## Offline rehearsal

Start a separate server with a fresh output directory and a free port in a
credential-free terminal. The server defaults to mock when requested by the UI;
there is no separate server `--offline` switch. `--mock` on the recorder keeps
the UI's Offline demo checked for both runs.

```sh
python -m plainproof serve --port 0 --timeout-s 420 --out runs/demo-mock
# In another credential-free terminal, use its printed URL:
python demo_video/record.py --base http://127.0.0.1:54321 \
  --web-out runs/demo-mock --repo . --mock --out demo_video/recordings/mock-take-01
python demo_video/tts.py --fallback
python demo_video/assemble.py \
  --events demo_video/recordings/mock-take-01/events.json \
  --tts demo_video/tts/manifest.json --out demo_video/demo-mock.mp4 --mock-watermark
python demo_video/verify.py --video demo_video/demo-mock.mp4 \
  --timeline demo_video/timeline.json --frames demo_video/test_frames \
  --expect-narration-timeline
python -m tests.run_tests
```

`--fallback` uses espeak-ng if found, otherwise silent clips at words/2.6 seconds.
It does not use `espeak`. Regenerate real TTS without `--fallback` before the real
assembly. The assembler refuses mock footage/fallback audio without
`--mock-watermark`. That option places a diagonal MOCK — NOT FOR SUBMISSION PNG
on every frame, including cards, and mock run badges do not claim real API use.

## Review before using the video

- Watch the entire movie with sound. Confirm every spoken line matches the
  script and captions, caption placement, pauses, model badges, and no clipped
  speech. Silent fallback is only a pipeline test.
- Verify repair uses the exact filmed failing run and its original app, the
  colored diff is visible, the original app is untouched, and FIXED is shown.
  Open the repair replay receipt: PASS, original FAIL, zero calls, USD 0, and
  0 items left. Check the original receipt link works. The timestamps and usage in events.json
  come from these receipts; no README benchmark is substituted.
- Inspect the fail receipt's plan, 1 items left evidence, and audit checkpoint.
  Inspect the new delete run's positive marker_seen and final checks. Real
  plans, latency, coordinates, and replay compatibility can vary; failed or
  weak evidence must be reviewed and re-recorded, never relabelled.
- Confirm the offline negative control shows INCONCLUSIVE / marker_never_seen
  with both final checks true. A result caption uses the recorded verdict/reason
  so the machine reason is visible alongside the receipt’s prose. Keep the
  offline badge visible in a real video.
- Check `verification.json`, the six extracted frames, and timeline.json. The
  verifier examines all six scenes and both cards; it saves six zoomed frames,
  including the colored diff and FIXED banner.
  ffprobe must report 170–180 seconds, 1920×1080, 30 fps, H.264 with AAC audio.
- Check no personal information appears in paths or footage. Keep exact receipts
  and raw recordings privately. No tool captures terminal history or reads a
  credential. Raw recordings, TTS, assembled movies, and render intermediates
  are ignored by git. Keep source changes uncommitted for review.

The Linux rehearsal cannot establish the real Meta plan/replay compatibility,
Mac say voice quality, or the exact rendering of your installed Mac fonts.
Those are the remaining real-run checks. The fixed story and closing-card text come from DEMO_SCRIPT.md. The repair
rehearsal uses the actual built flow with the offline mock fixer; it does not
validate real API behavior. This environment also differs from Python 3.15 and
ffmpeg 9 on the Mac, so those exact versions remain real-take checks.

## Evidence focus and silence checks

The receipt uses stable `#verdict`, `#checks`, `#steps`, `#plan`, and
`#run-details` sections. Checkpoint rows carry `data-check-id` and `data-source`;
steps carry `data-step`. The recorder locates counter expectations by their
literal text, so real planner IDs can differ from the mock IDs. After scrolling,
every inspection records its viewport rectangle, padded by 24 pixels and clipped
to the viewport, plus start/end times in `events.json` under `focus`.

Assembly maps those intervals into `timeline.json`, grows each rectangle to
16:9, and applies a 0.4-second cosine ease in six crop/scale steps before
the Pillow overlays. The final evidence stays zoomed through any held frame. Captions and badges stay fixed and readable while the
receipt zooms. Typing and evidence stay at normal speed; waiting is accelerated.
The 175-second cut distributes narration pauses throughout each scene, starts
speech over the title, and ends speech at the final six-second closing card.
It fails early if a take or voice cannot fit without a gap over four seconds.
Existing DEMO_SCRIPT.md lines remain verbatim; factual additions live in
narration.json with scene IDs.

For real audio, run verify.py **without** `--expect-narration-timeline`.
It runs `silencedetect=noise=-45dB:d=0.05` and fails if any silence longer than
4.0 seconds starts before the closing card. The fallback rehearsal flag checks
planned caption coverage instead, since fallback WAVs may contain only silence;
the report still includes the measured audio's longest silence so it cannot be
mistaken for a real voice check. Both modes require the evidence focus intervals
in timeline.json and save six zoomed evidence frames.
