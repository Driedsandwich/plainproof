Three-minute video plan for Plainproof, Meta Global AI Developer Hackathon, “Push the Frontier” (software), Oct 27–Nov 4, 2026. Narration is in English. Show the implemented planner, browser operator, judge, receipts, replay, web UI and Muse Spark 1.3 repair loop. Muse Code is an optional experimental fixer, not the default.

Record complete real runs, then edit to the timeline below. The supplied web counter run took 102 seconds and the delete run 64 seconds, so this cannot be a three-minute continuous take. Keep requirement entry and Run clicks at normal speed; accelerate waiting with a visible “Waiting accelerated” label or cut with “Elapsed time” shown from the receipt. Do not substitute mock footage for a real API run. Actual latency, model plans, and replay compatibility can vary; do not promise the supplied measurements will repeat.

Before recording, install dependencies and Playwright as in README.md, activate `.venv`, and provide `MODEL_API_KEY` to the process through the environment off camera. From the repository root:

```sh
python -m plainproof serve --port 8765 --out runs/demo-web
```

If port 8765 is already in use, pick any free local port and use it in the URL. Open `http://127.0.0.1:8765`. Keep the first web tab on the counter FAIL run so its Propose a fix button remains available for the closing scene. Use a second terminal with the same virtual environment for replay, and a second web tab for the later delete run. Reserve a fresh `runs/demo-replay` output directory. Pre-open `test-artifacts/delete-noop/receipt.html` in a separate tab after generating it with `python -m tests.run_tests`; this is an explicitly labelled offline negative control for the marker guard. The tests overwrite `test-artifacts/`, so finish them before filming. Real receipts remain in `runs/demo-web/`.

| Time | Exact command or UI action | Must be visible | English narration |
| --- | --- | --- | --- |
| 0:00–0:20 | Show the Plainproof UI with the requirement field. Keep the repository's `README.md` diagram available in another tab. | Product name, requirement field, and the requirement-to-receipt flow. | “People directing AI coding agents may not be able to audit code, but they can say what their app must do. Plainproof tests that requirement and gives them evidence they can inspect.” |
| 0:20–0:32 | Select **Bug: completed items still counted** (`bug_counter`). Replace the requirement by typing **After I add one to-do and mark it done, the counter decreases to 0 items left.** Keep **muse-spark-1.3**, uncheck **Offline demo**, check **Approve generic computer confirmations for this local app only**, and click **Run proof**. | App choice, typed requirement, selected model, offline checkbox cleared, local approval checked, and the Run click. | “I choose the broken counter app and type the behavior I need. This is a real Meta Model API run, with generic confirmations approved only for this local app.” |
| 0:32–1:08 | Watch the same UI run. Accelerate or cut only the waiting, with a visible label. | Streamed browser screenshots, executed steps adding the marker and marking it done, and the completed item alongside **1 items left**. | “Muse Spark 1.3 plans structured checks, then uses the native computer tool to operate the browser. Screenshots and steps arrive as it works.” |
| 1:08–1:20 | Wait for **FAIL**, then click **Open receipt**. | FAIL in the UI and receipt; **Checks and evidence** containing the exact snippet **1 items left**. | “The model does not decide success. The counter still says ‘1 items left,’ and Python's page-text checks return FAIL.” |
| 1:20–1:30 | In the receipt, show **Plan**, **Checks and evidence**, then the audit checkpoint. Copy only the 16-character run ID from `/runs/<id>/receipt.html` for the next shot. | Planner-chosen checks, screenshot and evidence, and the audit reload. | “The receipt exposes the checks the planner chose and the evidence from an independent reload. You can inspect what PASS or FAIL actually means.” |
| 1:30–1:43 | Keep the original web tab on FAIL. Switch to the second terminal and run the replay commands below, entering the ID of the failing receipt just shown. | The CLI replay command pointing at that same receipt and `demo_app/correct`. | “Now I replay the failing receipt against the correct app. The CLI reuses its executed actions and checks against this version.” |
| 1:43–2:10 | Open `runs/demo-replay/receipt.html` directly in the browser. Show its heading and original-receipt link; scroll to **Run details** and the counter evidence. Use the JSON summary command below if needed. | **PASS**, **0 items left**, original verdict FAIL, `model_calls` with planner 0 and operator 0, and `usd` 0. | “The same recorded test now passes. Replay makes zero model calls and costs zero dollars in model usage. It repeats the recorded coordinates; it does not ask a model to try again.” |
| 2:10–2:19 | Open a second web UI tab and select **Correct**, leaving the original FAIL tab untouched. Type the new requirement **After I add one to-do and then delete it, it is gone from the list and the counter shows 0 items left.** Confirm offline mode is still unchecked and local approvals enabled; click **Run proof**. | New requirement being typed and the new Run click; no preset delete button. | “Here is a new requirement, typed live: add one item, delete it, and leave zero items.” |
| 2:19–2:31 | Show the actual add/delete screenshots and steps from that run, with waiting visibly accelerated. On **PASS**, open its receipt. Show the marker in an earlier screenshot, `marker_seen: true` in the action sequence, then absence and **0 items left** at the end. | The new run's PASS, planner checks, positive marker sighting, final absence, and zero counter. | “It passes after the item appears and is deleted. The receipt records that the marker was actually seen before it vanished.” |
| 2:31–2:40 | Switch to the pre-opened `test-artifacts/delete-noop/receipt.html`. Overlay **Offline negative control — operator does nothing**. Show the heading, reason, and passing final checks. | **INCONCLUSIVE**, **marker_never_seen**, and both final checks marked true on the untouched empty page. | “This offline no-op control has the same passing final checks, but never creates the item. The marker guard makes it inconclusive, preventing a vacuous pass.” |
| 2:40–3:00 | Return to the original counter FAIL web tab. Click **Propose a fix**. Show the diagnosis and colored diff, then the **FIXED** banner. Open the replay receipt and its Run details. Accelerate waiting with a visible label; retain the exact repair artifacts. | FAIL receipt → Propose a fix → app diff → FIXED; replay **PASS**, original FAIL link, **Model calls: 0** and **USD 0**. | “Muse Spark 1.3 now proposes a minimal fix from this failure. Plainproof applies it to a throwaway copy and replays the same path. Replay passes with zero model calls, so the fix is accepted. The original app stays untouched. Built for this hackathon; Muse Code is an optional experimental fixer.” |

Replay commands for the 1:30–2:10 segment (the ID is from the actual web receipt, not a placeholder to paste unchanged):

```sh
read -r -p 'Failing web receipt ID: ' PP_RECEIPT_ID
python -m plainproof replay "runs/demo-web/$PP_RECEIPT_ID" \
  --app-dir demo_app/correct --out runs/demo-replay
python -c "import json; from pathlib import Path; r=json.loads(Path('runs/demo-replay/receipt.json').read_text()); print(r['verdict'], r['model_calls'], 'USD', r['usd'])"
```

The replay HTML can be opened directly from the file manager or the browser's Open File dialog. The web UI does not provide a replay button or serve this CLI replay directory. `--app-dir demo_app/correct` selects the standalone replay target. The closing repair instead patches a copy of the original failing counter app; do not switch its source to Correct. The measured **1.3 s** replay in README.md was for the reload receipt; do not label this counter replay with that timing unless it measures that value.

Recording checklist:

- Capture a 1920 × 1080 frame; keep the UI browser content at least 1280 × 900 so the controls and streamed screenshot are legible. The controlled app viewport stays at 1024 × 768 for replay.
- Hide personal browser bars, bookmarks, profile menus, notifications, and unrelated terminal tabs. Use a clean recording window.
- Set the credential off camera. Do not show environment dumps, secret-manager contents, shell history, or credential values.
- Use only synthetic to-do markers; no personal data in the app or receipts.
- Keep receipts from the exact filmed runs, the original and replay link, repair JSON/HTML/diff and patched copy, and unedited recordings. Label offline controls and compressed waiting clearly.
- Check the real run produced the intended checks and marker sighting before using its footage. If a plan is weak, errors, or finishes INCONCLUSIVE, retain that honest result and record another attempt; do not relabel it PASS.
- Show the repository's dated commit history as it is; do not imply more history than exists.

The closing scene uses the built Spark repair flow, not Muse Code and not a prepatched Correct app. Keep Offline demo unchecked for the real recording; label any separately recorded mock demo explicitly. Repair timing and cost must come from the actual filmed run; no repair measurements are supplied. The optional fresh check is separate and is not needed for this scene.
