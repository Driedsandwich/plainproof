# Record Plainproof on the Mac

Run commands from the repository root. The server is the only process that
needs the Meta credential. Use a separate terminal that has no credential set
for all demo tools. These scripts never load `.env`, inspect environment values,
request a key, or pass a key to a subprocess. Do not print environment variables.
The replay and offline control run as real CLI subprocesses from that terminal.

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
is rendered with Pillow. The only video filters used are scale, pad, setpts,
concat, and overlay; audio is assembled as padded PCM using Python's wave module.

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
python -m plainproof serve --port 0 --out runs/demo-real
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

Do not pass `--mock` for the real take. Both paid web runs use the selected
muse-spark-1.3 model, clear Offline demo, and enable local confirmation approval.
The server handles the credential independently. The recorder waits up to seven
minutes for each receipt; the repository's server run budget is currently 180
seconds. It does not change this budget or retry paid runs automatically.

The recorder opens one headless 1280×900 page, types at 35 ms per key, records
receipts, executes the replay, and creates the explicitly offline no-op control
using `python -m plainproof check --mock --mock-scenario noop`. This scenario
matches `tests.run_tests.NoOpOperator`: one wait action, then a completion claim.
`INCONCLUSIVE` intentionally exits 2. Replay must exit 0 and produce PASS, zero
planner/operator calls, and USD 0. Unexpected verdicts stop recording and retain
the raw take plus an error in events.json; review the honest result and use a
fresh take/output directory for another attempt.

The CLI command displayed on screen includes the actual interpreter and absolute
paths, plus actual stdout/stderr/exit code. Choose a repository location without
private names if those paths should not appear in the submission. The recorder
never substitutes predicted command output. CLI runs may briefly hold the prior
page; those intervals are logged as waiting. A pair of solid-color pre-roll
swatches calibrates the event clock against encoded frames (about 0.12 s
precision). The swatches are excluded from the assembled movie.

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
card is 6 seconds. Between them, scene lengths accommodate actual narration
and every normal-speed typing/result/inspection interval. Only waiting is
accelerated, with a visible rounded factor badge; spare time holds the final
scene frame. If narration or normal-speed footage cannot fit, assembly fails
rather than speeding typing/results or truncating speech. Increase TTS rate
slightly or re-record shorter inspection pauses if necessary, then review.

## Offline rehearsal

Start a separate server with a fresh output directory and a free port in a
credential-free terminal. The server defaults to mock when requested by the UI;
there is no separate server `--offline` switch. `--mock` on the recorder keeps
the UI's Offline demo checked for both runs.

```sh
python -m plainproof serve --port 0 --out runs/demo-mock
# In another credential-free terminal, use its printed URL:
python demo_video/record.py --base http://127.0.0.1:54321 \
  --web-out runs/demo-mock --repo . --mock --out demo_video/recordings/mock-take-01
python demo_video/tts.py --fallback
python demo_video/assemble.py \
  --events demo_video/recordings/mock-take-01/events.json \
  --tts demo_video/tts/manifest.json --out demo_video/demo-mock.mp4 --mock-watermark
python demo_video/verify.py --video demo_video/demo-mock.mp4 \
  --timeline demo_video/timeline.json --frames demo_video/test_frames
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
- Verify the exact failing run ID is replayed against `demo_app/correct`, the
  original receipt link works, and the replay receipt says PASS, original FAIL,
  zero calls, USD 0, and 0 items left. The timestamps and usage in events.json
  come from these receipts; no README benchmark is substituted.
- Inspect the fail receipt's plan, 1 items left evidence, and audit checkpoint.
  Inspect the new delete run's positive marker_seen and final checks. Real
  plans, latency, coordinates, and replay compatibility can vary; failed or
  weak evidence must be reviewed and re-recorded, never relabelled.
- Confirm the offline negative control shows INCONCLUSIVE / marker_never_seen
  with both final checks true. It must stay labelled as offline in a real video.
- Check `verification.json`, the six extracted frames, and timeline.json. The
  verifier examines all eight scenes; it saves the six action/evidence scenes.
  ffprobe must report 170–180 seconds, 1920×1080, 30 fps, H.264 with AAC audio.
- Check no personal information appears in paths or footage. Keep exact receipts
  and raw recordings privately. No tool captures terminal history or reads a
  credential. Raw recordings, TTS, assembled movies, and render intermediates
  are ignored by git. Keep source changes uncommitted for review.

The Linux rehearsal cannot establish the real Meta plan/replay compatibility,
Mac say voice quality, or the exact rendering of your installed Mac fonts.
Those are the remaining real-run checks. The hackathon/date statements come
verbatim from DEMO_SCRIPT.md; no repair feature or simulated repair is added.
