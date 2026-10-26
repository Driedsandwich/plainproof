# Plainproof minimal harness

Measures one requirement: a newly added to-do survives a page reload. The model operates from screenshots; Python reads the DOM and decides the verdict. Prepared for the Oct 27–Nov 4, 2026 Meta hackathon. **Only offline mocks were run; this does not measure Muse Wonder's actual performance yet.**

Python 3.12+; the only runtime packages are `openai` and `playwright`:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python tests/test_offline.py
python plainproof_min.py --mock --variant buggy --out runs/my-buggy
python plainproof_min.py --mock --variant fixed --out runs/my-fixed
```

On Linux, missing browser libraries/fonts normally need `python -m playwright install-deps chromium`. Pytest is optional: if installed, `python -m pytest -q` runs the same plain-assert tests. This container lacked libraries and fonts and had no sudo access: Ubuntu packages were extracted into `/tmp/plainproof-browser-deps/root` and fonts installed in the user's font directory. Verification here used `LD_LIBRARY_PATH=/tmp/plainproof-browser-deps/root/usr/lib/x86_64-linux-gnu`.

A real run uses an already-set `MODEL_API_KEY` environment variable:

```bash
python plainproof_min.py --variant fixed --out runs/live-fixed --max-steps 25 --timeout-s 600
```

The key is never printed or saved. API exceptions are logged by class only. Mock runs neither require a key nor construct an API client. `--mock-scenario safety` and `--mock-scenario never_reload` select the two negative control scripts. Each run needs a fresh output directory.

The variants differ **only at line 39** of `app/fixed/index.html` and `app/buggy/index.html`:

```javascript
// fixed, line 39:
  localStorage.setItem('todos', JSON.stringify(items));
// buggy, line 39:
  // Injected defect: omit saving newly added items to localStorage.
```

Both load saved items identically at line 26. The form's submit handler supports Add and Enter. Each run gets a fresh browser context, loopback HTTP origin, and unique `check-…` marker. Chromium is Playwright's own headless browser; screenshots are viewport-only, 1024×768 at scale 1. Browser requests are restricted to the app page, popups closed, and dialogs dismissed. DOM coordinates are supplied only to the mock; the real model receives images and the task.

Verdicts require a model-requested reload **after Python observed the marker in the list**. A missing addition or qualifying reload gives INCONCLUSIVE. Otherwise, absence after any subsequent reload gives FAIL; persistence through those reloads gives PASS. On a final assistant message, Python also reloads and audits once; this audit cannot substitute for the model's reload. Safety checks stop immediately with `stopped_safety_check`, without actions, acknowledgement, or an audit. Budget stops retain existing evidence. The model's prose never controls these rules.

Each output directory contains:

- `verdict.json`: verdict, full model claim, agreement, step count, elapsed seconds, token sums, USD estimate, stop reason, marker, reload observations, and safety checks.
- `trajectory.jsonl`: response IDs, planned/executed actions, error classes, and checks; no image payloads. Setup errors use a null response ID.
- `shots/step00.png`, `step01.png`, …: initial and post-batch screenshots; `audit.png` is the final audit. Additional calls in a turn get `-callNN` suffixes.

`steps` counts action-bearing response turns; a final answer is logged but does not consume a step. Agreement uses conservative phrase matching for clear persistence/loss claims; ambiguous or conflicting prose, missing claims, and nonbinary verdicts produce `agrees: null`. It is an annotation, never verdict evidence. Timeout covers browser setup, requests, actions, and audit; resource cleanup can add a little wall time.

Official references were read on 2026-10-27:

- [Computer use](https://dev.meta.ai/docs/computer-use.md), **Enable the computer tool**: “The computer tool takes only a `type`”. **Run the loop**: “Chain turns with `previous_response_id` so the server keeps conversation state”. **Set a strict system prompt**: “Do not guess hidden UI state”. The harness uses `instructions`, `tools=[{"type":"computer"}]`, ordered `actions`, and matching `computer_call_output.call_id` with a fresh `computer_screenshot` data URL. It stops each failing batch early and records pending checks without acknowledging them. Default server-managed storage supports chaining.
- [Cookbook](https://dev.meta.ai/docs/cookbook/computer-use.md), **Prompting tips**: “Give a goal, not a script.” The live prompt states a goal and allowed reload keys; only the offline fake follows a script.
- [Pricing](https://dev.meta.ai/docs/pricing-rate-limits.md), **Model pricing → Muse Wonder**: “Cached input | $0.20”, “Input | $4.00”, “Output | $20.00”, per million tokens in Standard mode. Every response contributes to usage, including safety/final turns. USD = `((input-cached)*4 + cached*0.20 + output*20)/1_000_000`; mock usage/cost is zero.
- [Prompt caching](https://dev.meta.ai/docs/prompt-caching.md), **See what was cached**: cached tokens are a “subset of your input tokens”. This section and [Responses → Retrieving a response](https://dev.meta.ai/docs/protocols/responses.md) establish `usage.input_tokens_details.cached_tokens`, `input_tokens`, and `output_tokens`.

Uncertainties checked rather than assumed: **Read and execute actions** lists key strings but no exhaustive aliases or wait duration. The driver accepts Playwright key names plus common Ctrl/Cmd aliases, maps F5/Ctrl+R/Cmd+R to `page.reload()`, and waits 250 ms for `wait`; no undocumented API fields are sent. Mouse `wheel` maps to middle-click and back/forward to browser history. The cookbook uses Spark/OpenCode/Cua, rather than the native Wonder Responses protocol; the computer-use guide supplies that protocol. Missing usage fields count as zero, so incomplete accounting can underestimate cost. Live service/account compatibility remains untested.

Offline verification output (Python 3.14.4; pinned packages in `requirements.txt`; Chromium installed with Playwright):

```text
$ python tests/test_offline.py
test_buggy: OK
test_fixed: OK
test_safety: OK
test_never_reload: OK
test_step_limit: OK
test_timeout: OK
test_model_claim_does_not_decide_verdict_and_cached_cost: OK
test_action_error_stops_batch: OK
test_reload_aliases: OK
test_variants_have_one_defect: OK
test_multiple_calls_keep_order_and_matching_outputs: OK
11 offline tests passed

$ python -m pytest -q
...........                                                              [100%]
11 passed in 14.86s
```
