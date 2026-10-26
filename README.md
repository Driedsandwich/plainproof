# Plainproof — stage 1

**Proof before you accept an AI-built change.** Write a plain-language requirement for a running web app. Meta Muse plans a screenshot-driven test, operates the browser, and leaves a receipt. Python evaluates the visible page state and owns the verdict. Replay the same successful actions after a fix, without contacting a model.

Built for the Meta Global AI Developer Hackathon, **Push the Frontier**, software; building period October 27–November 4, 2026. Stage 1 was newly written in this workspace on October 27, 2026. The supplied, same-day harness is the basis for the action driver and protocol; no other project's source code was copied. Dependencies are `openai`, `playwright`, and Python's standard library (plus the dependencies those two packages install).

## Run it

Python **3.12+**. Use only Playwright's own headless Chromium; no extensions, desktop browser, or existing profile.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --only-shell chromium
# On Linux, if shared libraries are missing:
python -m playwright install-deps chromium

python -m plainproof check --app-dir demo_app/bug_reload \
  --requirement 'A new to-do is still in the list after I reload the page.' \
  --out runs/before --mock
# FAIL returns exit code 1; continue with the replay command:
python -m plainproof replay runs/before --app-dir demo_app/correct --out runs/after
# PASS, zero model calls

python -m plainproof check --app-dir demo_app/bug_counter \
  --requirement 'After adding one to-do and marking it done, the counter decreases to 0 items left.' \
  --out runs/counter --mock

python -m plainproof serve --port 8765 --out runs/web
# Open http://127.0.0.1:8765
```

The web UI starts in offline mode. Pick a variant, enter a requirement, and press **Run proof**. It streams the latest screenshot, plan, and steps through server-sent events, then links the receipt. For real runs, supply `MODEL_API_KEY` in the server/CLI process environment and turn off offline mode. There is no credential input field, dotenv reader, configuration file, or credential output. Do not put credentials in requirements or application content.

A real check (not executed during this build):

```sh
# Supply MODEL_API_KEY through your environment/secret manager first.
python -m plainproof check --app-url http://127.0.0.1:3000/ \
  --requirement 'A new to-do is still in the list after I reload the page.' \
  --out runs/live --model muse-spark-1.3 --approve-confirmations
```

`check` requires exactly one of `--app-dir` / `--app-url`. `replay` accepts either too, although the principal demo uses `--app-dir`. Both accept `--max-steps` (default 25) and `--timeout-s` (default 180). Output directories must be fresh and empty. Exit codes: PASS 0, FAIL 1, all other verdicts/setup failures 2. The selectable operator models are `muse-spark-1.3` (default) and `muse-wonder-1.0`; the planner always uses Spark 1.3. `--mock` never instantiates an API client; replay never instantiates one even without `--mock`.

Offline planning recognizes reload, counter, and delete demos. Its operator uses DOM-derived coordinates only to script the mock. Real model inputs contain the requirement/task and viewport screenshots, never DOM selectors or page text. Offline scripts are integration fixtures, not evidence of general model ability.

## Who decides pass or fail?

1. The planner receives the requirement and first 1024 × 768 screenshot. It requests a JSON schema through Responses API `text.format`, with `strict: true`. `planner.py` also validates the complete schema locally: exact keys and types, allowed kinds/timing, nonnegative integer counts, nonempty text/checks, and unique IDs. Invalid JSON, schema violations, or incomplete planner responses produce **ERROR**, with no operator call and no fallback plan.
2. The model chooses `agent_task` and the checks. Any literal to type is `{MARKER}`. The harness replaces it in the task and checks with a unique per-run `check-…` marker; receipts retain both the template and resolved plan. **The planner chooses what to test; its test selection can be incomplete or wrong.** A PASS proves only those recorded checks on this trajectory.
3. The operator runs the native computer tool with a fixed viewport. The driver implements click, double-click, drag, keypress, move, scroll, type, wait, and screenshot. F5, Ctrl+R and Cmd+R map explicitly to `page.reload()`, in checks and replay alike. A dismissed dialog interrupts the remaining batch. Unknown actions, action failures, or unexpected navigation stop with ERROR.
4. After each mapped reload the driver records `{source, page_text, screenshot}` plus a checkpoint ID. `page_text` is exactly `document.body.innerText`. It records the end state before a final **audit reload**, then records that reload too. A successful model finish always triggers this independent audit. Safety/budget stops preserve available evidence and do not trigger an audit.
5. The judge uses **case-sensitive literal substring matching**, with no whitespace normalization, regex, CSS selectors, or model judgment. `text_visible`: at least one occurrence; `text_absent`: zero; `text_count`: exactly `count` non-overlapping occurrences. `after_each_reload` checks run at every model reload and audit checkpoint; `end` checks run at the end checkpoint **and** the audit checkpoint. Thus the audit also checks that the final state survives a reload.
6. **PASS** requires normal completion, coverage of every check, and all evaluated instances passing. **FAIL** requires normal completion, coverage, and at least one failed instance. Earlier failures remain failures after later success. Missing coverage, no operator reload for an `after_each_reload` check, empty/incomplete operator replies, step exhaustion, and timeouts are **INCONCLUSIVE**. A refused safety check is **stopped_safety_check**. Setup/runtime/planner failures are **ERROR**. Errors and safety stops take precedence over observations.

The final model claim is stored. A deliberately conservative phrase classifier records PASS/FAIL/unknown and agreement with the code verdict; ambiguous claims stay unclassified. This comparison never affects the verdict. Mocks intentionally claim success on failing apps to verify this separation.

## Receipts and replay

Every started run writes `receipt.json` and `receipt.html`, with separate PNGs under `shots/`. JSON contains the requirement, template/resolved plan, proposed and actually executed actions, pending/acknowledged safety checks, screenshot paths, visible-text checkpoints, check evidence snippets and actual counts, verdict, claim comparison, model names, per-role usage/cost, elapsed seconds, and budgets. It contains no screenshot data URLs or base64. Errors store only their type and HTTP status because free-form API errors can expose request bodies or credentials. Strings are additionally redacted against the credential before serialization/events.

`receipt.html` is genuinely a standalone file: inline styles and percent-encoded PNG images, no remote resources, scripts, or base64. Screenshots also remain as PNG files next to the JSON. JSON page text is the complete evidence; snippets in check results are only a convenient view. Rendered text is HTML-escaped. Receipts can contain application data; inspect them before publishing.

Replay reads the resolved checks, marker, viewport, and **executed** actions. It uses the same driver in a fresh browser context against the new app, repeats reload/end/audit checkpoints, and writes a new receipt with the original file link, SHA-256, verdict, and original acknowledgements. Proposed actions that never executed do not replay. An incomplete or safety-stopped source cannot become a complete passing run by replay alone. There are no planner or operator calls and the replay usage/USD are zero. An original final claim is retained as provenance, not attributed to a new model run.

## Safety policy

Only `computer_action_requires_confirmation` may be acknowledged, and **only** when `--approve-confirmations` is explicitly enabled and the page is still the exact entry URL (or its `index.html`) of the sandboxed **local** app. Any other code, a mixed list containing another code, missing approval, or a different/nonlocal page stops the run **before those actions execute**. Every acknowledgement preserves the full check object in the receipt and is echoed on the matching `computer_call_output`. Replay records original acknowledgements as provenance; it obtains no new model safety decisions.

App servers and the UI bind **127.0.0.1 only**. Each check/replay gets an isolated browser context with fresh storage, fixed viewport, blocked service workers, no accepted downloads, dismissed dialogs, and closed extra pages. HTTP(S) requests and WebSockets to every origin except the app's are blocked. Same-origin assets/routes are allowed; confirmations use the narrower exact-page policy. The UI rejects foreign Host/Origin requests and serializes runs. This is an app-testing boundary, not a hardened VM for hostile JavaScript; the allowed app origin itself must be trusted. Real API requests from the Python client are separate from browser routing.

## Measurements and pricing

These are the builder's **real API measurements on October 27**, supplied with the task, not new measurements made by this implementation:

| Experiment | Result |
| --- | --- |
| Original persistence bug | FAIL 2/2 |
| Original fixed app | PASS 2/2 |
| Operator trajectory | 3–5 steps |
| Elapsed time | 34–59 seconds/run |
| Cost | $0.024–0.037/run |
| Wonder native computer tool on this account | HTTP 400, `Unsupported tool type: 'computer'` |
| Spark native computer tool | Accepted; generic confirmation attached to every action |
| Transient error | One HTTP 500 mid-trajectory |

The client uses `base_url='https://api.meta.ai/v1'`, only the environment credential `MODEL_API_KEY`, and `max_retries=2`, retaining the supplied harness's SDK-managed retry configuration. An unrecovered error produces ERROR and keeps prior evidence. Stage 1 adds a planner call, so the original per-run costs are not an estimate of the complete new flow.

Standard USD per million tokens, from [Model pricing → Muse Spark / Muse Wonder](https://dev.meta.ai/docs/pricing-rate-limits.md):

| Model | Input | Cached input | Output |
| --- | ---: | ---: | ---: |
| muse-spark-1.3 | $1.25 | $0.15 | $4.25 |
| muse-wonder-1.0 | $4.00 | $0.20 | $20.00 |

For each role, estimated USD = `((input − cached) × input_rate + cached × cached_rate + output × output_rate) / 1,000,000`. Both roles' usage is retained separately, then costs are summed. Missing usage is treated as zero; costs are estimates of reported usage, not a billing reconciliation. Offline runs report zero usage and USD.

## Official references read and exact sections used

Read on October 27, 2026 before implementation; only brief prose quotations are reproduced, no documentation example code was copied.

- [Build a computer-use agent](https://dev.meta.ai/docs/computer-use.md): **How the observe-act loop works; Enable the computer tool; Read and execute actions; Return a screenshot; Handle safety checks; Run the loop; Manage conversation state; Handle done and incomplete; Set a strict system prompt**. Quotes relied on: “The computer tool takes only a `type`” and “Acknowledgement asserts that you reviewed the action and accept it.” These support `tools=[{"type":"computer"}]`, the action vocabulary, `computer_call_output`, full acknowledgement objects, screenshot transport, `previous_response_id`, terminal messages, and fixed screenshot coordinates. No display/environment fields, truncation options, or stateless safety-replay fields are added.
- [Structured output](https://dev.meta.ai/docs/structured-output.md): **Understand how it works**, specifically **text.format vs response_format**; **Stay within schema constraints**; **Enforce strict mode**; **Combine with other features**. Quote: “`text.format` is the Responses API parameter for structured output.” These support Responses formatting, screenshot input, strict objects with all properties required and no extra properties, and nested `anyOf` for the three check shapes. The count property exists only for `text_count`.
- [Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits.md): **Pricing modes → Standard mode; Model pricing → Muse Wonder / Muse Spark / Token pricing notes**. Quote: “Cached input costs less”. These supply the rates above and separate cached-token accounting. No Contributor mode is used.
- [Responses API](https://dev.meta.ai/docs/protocols/responses.md): **How it works; OpenAI compatibility notes**, checked additionally for typed input items and the `text.format` endpoint distinction.

### Uncertainties and limits (not guessed away)

- The computer-use guide describes Wonder support, but the supplied account measurement rejected it. Availability may vary by account; Spark is the default, Wonder remains selectable, and errors are recorded without a silent model switch.
- The docs do not define an exhaustive list of safety codes or guarantee when generic confirmations appear. Only the measured code is eligible for the narrow opt-in policy. All other codes stop.
- The structured-output guide's worked examples mainly use Chat Completions. Its endpoint note and strict-mode table specify Responses `text.format`; the implementation follows those and the compatible SDK's named schema shape. Its real Meta request and nested `anyOf` schema have **not** been tested against the service during this build. Local schema validation is mandatory regardless of advertised server guarantees.
- Pending model responses, interrupted actions, app-initiated navigations, or asynchronous UI work can leave incomplete evidence. The fixed wait action is 250 ms; the operator must wait for visible settled state. Explicit F5/Ctrl+R/Cmd+R reloads are instrumented; arbitrary application-internal navigation is not inferred as a test reload.
- The checks observe visible body text, not accessibility semantics, checkbox state, hidden storage, backend state, or styling. Substring counts can collide with labels or repeated text. Marker uniqueness reduces collisions for typed content. End/audit text alone cannot establish every causal precondition; review the action sequence and chosen checks.
- Replay is a coordinate replay from a clean browser context. Changed layout, different data, timing, or viewport-dependent content can invalidate the path; replay does not adapt. SHA-256 links provenance but is not a signature or tamper-proof attestation. Public app receipts may reveal user data.
- Server-managed operator history uses `previous_response_id` and its documented storage default. Only local browser storage is fresh; a supplied app URL may retain server-side data. No login, arbitrary file upload, CI integration, deployment, or full browser-security sandbox is provided.
- This build called **no real Meta API**. All assertions below use mocks and Playwright's headless Chromium, so general planner quality, latency, account compatibility, and full-flow real cost remain unmeasured.

## Exact planted-bug diffs

Both variants are copied only from the newly written `demo_app/correct/index.html` in this build. Everything except the following lines is identical; neither variant has conditional runtime flags or hidden fixes.

```diff
--- demo_app/correct/index.html
+++ demo_app/bug_reload/index.html
@@ -56,7 +56,7 @@
   const text = input.value.trim();
   if (!text) return;
   items.push({text, done: false});
-  save();
+  // PLANTED BUG: skip saving newly added items.
   input.value = '';
   render();
 });
```

```diff
--- demo_app/correct/index.html
+++ demo_app/bug_counter/index.html
@@ -32,7 +32,8 @@
 let items = JSON.parse(localStorage.getItem('todos') || '[]');
 function save() { localStorage.setItem('todos', JSON.stringify(items)); }
 function render() {
-  document.querySelector('#counter').textContent = `${items.filter(item => !item.done).length} items left`;
+  // PLANTED BUG: completed items still count as unfinished.
+  document.querySelector('#counter').textContent = `${items.length} items left`;
   list.replaceChildren(...items.map((item, index) => {
     const li = document.createElement('li');
     const toggle = document.createElement('input');
```

## Tests and environment

Run `python -m tests.run_tests`. No pytest is used. The suite rejects real client construction on check/replay paths, injects a sentinel environment credential, and exercises real browser interactions and HTTP/SSE. It regenerates `test-artifacts/` (ignored by git); save receipts elsewhere if you want to retain them.

This workspace used Python 3.14.4, openai 3.39.0, Playwright 1.64.1 and its Chromium headless shell. Python 3.12 compatibility is targeted by syntax/API choice but was not separately executed. System browser libraries were missing and sudo required a password. For this test session only, Ubuntu shared-library packages were downloaded/extracted into `/tmp/plainproof-browser-libs/root`, then supplied through `LD_LIBRARY_PATH`; no personal browser or extension was used. On a typical Linux host use the Playwright `install-deps` command above.

Executed here:

```sh
LD_LIBRARY_PATH=/tmp/plainproof-browser-libs/root/usr/lib/x86_64-linux-gnu \
  .venv/bin/python -u -m tests.run_tests
```

Actual test output follows:

```text
PASS reload / bug_reload => FAIL
PASS reload / correct => PASS
PASS counter / bug_counter => FAIL
PASS counter / correct => PASS
PASS replay bug receipt / correct => PASS; zero model calls, USD 0
PASS non-confirmation safety check => stopped_safety_check; no actions
PASS confirmation approval=False => stopped_safety_check
PASS confirmation approval=True => PASS
PASS invalid planner JSON => ERROR; no operator calls
PASS missing model reload => INCONCLUSIVE
PASS step and time budgets => INCONCLUSIVE
PASS Ctrl+R and Cmd+R => reload checkpoints
PASS add / delete / reload => PASS
PASS schema validation rejects extra keys, bad kinds, bool counts, duplicate IDs, empty checks
PASS every checkpoint is evaluated (later success cannot mask earlier failure)
PASS foreign-origin requests blocked; confirmations restricted to exact local app pages
PASS loopback web UI: POST -> live SSE screenshots/steps -> FAIL -> receipt; cross-origin POST denied
PASS rendered web UI: Run -> FAIL -> screenshot -> standalone receipt; all embedded images decode
PASS python -m plainproof: mock check exits 1/FAIL; model-free replay exits 0/PASS
PASS all receipts: no credential, no base64; HTML includes standalone PNG images
ALL TESTS PASSED (offline; Playwright headless Chromium only)
PASS focused app-url check: URL without trailing slash; local confirmations recorded
```
