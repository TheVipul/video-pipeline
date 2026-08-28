# Defect log

Issues found by running the pipeline against live APIs, and what was done
about each. Every fix has a regression test unless noted.

The unifying theme: **almost all of these failed silently.** The pipeline
reported `Published: 5 | Failed: 0` while the AI layer had never executed, the
watermark was skipped, and four of five videos had been discarded from state.
Loud failure is a feature; quiet success is the dangerous mode.

---

## 1. AI layer had never run — wrong endpoint

`LLM_BASE_URL` was `https://api.MiniMax.chat/v1`, which returns
`401 invalid api key` for international keys. Correct host is
`https://api.minimax.io/v1`.

Because failures degraded gracefully, every run reported success with
`LLM cost: $0.0000` and metadata that was a verbatim copy of YouTube's own.

**Fixed:** corrected `.env` and `.env.example`, with a comment explaining the
two hosts so it is not reintroduced.

## 2. AI layer failed on the configured model

`MiniMax-Text-01` rejects `response_format={"type":"json_object"}` with HTTP
400. The pipeline depends on that parameter for structured output.

**Fixed:** switched to `MiniMax-M2.7-highspeed`, verified to accept it.

## 3. Reasoning blocks broke JSON extraction

M2.7 emits `<think>…</think>` before its answer. That block often contains
example JSON, and the old extractor took `text[first_brace:last_brace]` — so
it parsed the model's scratch work, or failed outright with
`No JSON object in LLM response` on a perfectly good completion.

**Fixed:** strip reasoning wrappers, then scan for the first *brace-balanced*
object while respecting string literals and escapes. Falls back to
`json_repair` for unescaped inner quotes, and recovers truncated responses
(hit `max_tokens` mid-object) rather than discarding them.
**Tests:** `tests/test_llm_parsing.py` (20).

## 4. A video was published titled "reject"

The metadata prompt asks for a `brand_safety` verdict. The model sometimes put
that verdict in the `title` field instead. Nothing validated it, so a video
went out titled `reject`.

**Fixed:** titles are checked against the verdict vocabulary and fall back to
the original title. **Test:** `test_verdict_tokens_fall_back_to_original`.

## 5. The model's own rejection was parsed, then discarded

The metadata response's `brand_safety` field was read into a local and never
used. A video the model explicitly wanted to reject was still marked `safe`.

**Fixed:** folded in as a third opinion alongside the rules check and the
dedicated safety call; the most conservative verdict wins.

## 6. Off-brand content published with invented metadata

The root design flaw. The safety classifier answers *"is this harmful?"*;
beach drone footage is not. Nothing asked *"does this belong on this brand's
channel?"* — so unrelated videos were published with confident, fabricated
on-brand titles ("Cooking Basics: Getting Started in the Kitchen" for a video
of a man at a zoo).

**Fixed:** relevance is now scored separately from harm, with a per-brand
`policy.min_relevance` threshold. Below it, content is held rather than
published. Prompts were rewritten to instruct the model to judge the *original*
content honestly and never invent a fitting title for unfitting content.
**Tests:** `tests/test_safety_gate.py`.

## 7. "review" verdicts published anyway

Only `reject` blocked publication, so anything the model was merely unsure
about shipped.

**Fixed:** `review` also blocks by default, marked `held_for_review` with
artifacts retained for a human. Opt out per brand with
`policy.publish_on_review: true`.

## 8. Metadata stage had no resilience — the headline requirement

The brief requires handling "YouTube proxy and blocking behavior". The
downloader had a proper fallback ladder; the *metadata* stage had none, and it
runs first. One dead proxy ended the run before any of that logic executed.

Measured before: `Published: 0 | Failed: 1`.

**Fixed:** metadata now runs the same ladder — rotate proxies, always fall
back to direct, rotate player clients, jittered backoff — plus error
classification so permanently-dead videos fail fast instead of burning
retries. Measured after: `Published: 1 | Failed: 0`.
**Tests:** `tests/test_resilience.py` (18).

## 9. `--publisher youtube` crashed instantly

`get_publisher` splatted every kwarg into every publisher:
`TypeError: YouTubePublisher.__init__() got an unexpected keyword argument 'output_dir'`.
The code path had clearly never been executed.

**Fixed:** the factory filters kwargs against each constructor's signature.
**Test:** `test_builds_each_publisher_with_shared_kwargs`.

## 10. "Re-upload" was local file copy only

The brief says the pipeline must *re-upload*. S3 needed `boto3`, which was
absent from `requirements.txt`, and YouTube was a stub.

**Fixed:**
- `boto3` added; S3 upload verified against a real in-process S3 HTTP server
  (not a boto3 mock) — 4KB object, correct content type, manifest round-trip.
- YouTube publisher implemented for real: resumable upload via Data API v3,
  exponential backoff on 5xx/429, `privacyStatus: private` by default, explicit
  `selfDeclaredMadeForKids`. Needs OAuth credentials; without them it fails
  with setup instructions instead of a stack trace.

**Tests:** `tests/test_publishers.py` (12).

## 11. Watermark silently skipped

Watermarking used `drawtext`, which needs ffmpeg built with libfreetype.
Homebrew's ffmpeg 8.x has neither `drawtext` nor `subtitles`, so the watermark
was skipped on any such machine while the run still reported success.

**Fixed:** falls back to rendering the text to a transparent PNG with Pillow
and compositing with `overlay`, a core filter present in every build.
Verified visually on an extracted frame.

## 12. Four of every five videos vanished from state

`records` had no LangGraph reducer, so the default last-write-wins replaced
the entire dict on every node return. A five-video run ended with **one**
record; `final_state.json` and the HTML report were correspondingly wrong.

**Fixed:** explicit `merge_records` reducer, merging per video id and
field-wise within a record. Verified: 5 records retained, 5 rows in the report.

## 13. Cost accounting was wrong in two ways

`AIEnrichment.cost_usd` was assigned `cost_guard.spent` — the guard's
*cumulative* total — so summing per-video costs produced a triangular
over-count. Separately, the reject path never accumulated cost at all, so a
run that rejected everything reported `$0.0000` after paying for five LLM
round trips.

**Fixed:** per-video cost is now a before/after delta, and the reject path
accumulates it. A run rejecting all five now correctly reports ~$0.0146.

## 14. The test suite ran zero tests

Four `tests/test_*.py` files existed, but they were `print()` scripts with no
test functions and no assertions. `pytest` reported **"no tests ran"** while
the project appeared to have tests.

**Fixed:** moved to `tests/manual/smoke_*.py` (documented as exploratory
scripts) and written a real suite: **80 assertion-based tests**, no network
required, covering every defect above.

## 15. Secret-leak risk

`.gitignore` covered `.env` but not `.env.*`, and the YouTube publisher
introduces `client_secret.json` / `youtube_token.json`.

**Fixed:** `.gitignore` now covers `.env.*` (excepting `.env.example`) and
both OAuth artifacts.

## 16. Windows-only setup

The bundled `.venv` was a Windows tree (`Lib/`, `Scripts/`) and the README
quickstart was PowerShell-only — unusable on macOS/Linux without a rebuild.

**Fixed:** added `setup.sh`, documented both platforms, removed the stale venv
and the `outputs_old*/` directories.

## 17. `--checkpoint` crashed on use

Resuming from a saved state died with
`NameError: name 'log' is not defined` — `agent/graph.py` referenced a module
logger it never imported, so the documented resume flag had clearly never been
exercised. The same block also swallowed a corrupt checkpoint and silently
restarted from scratch, which would re-download and re-pay for everything the
operator was trying to skip.

**Fixed:** logger imported; a missing checkpoint now raises a clear
`FileNotFoundError` and an unreadable one a clear `ValueError`, and a
successful resume logs how many records it restored and where it is resuming.

## 18. Only two brand configs existed

The walkthrough script and README referred to a `backcountry` brand that was
never created, so the multi-brand claim could not be demonstrated live.

**Added:** `configs/brands/backcountry.yaml` — its own audience, tone,
watermark, prompts and relevance threshold. Verified: it holds the same demo
videos that `generic` publishes, because none of them are outdoor-gear content.

---

# Round two — deeper code review

Found by reading the code rather than running it, then verified by running it.

## 19. Every stage reported the same failure status

`metadata_failed` was assigned on failures in **metadata, AI, download and
transform** alike, and publish used a bare `failed`. So neither the saved
state nor the HTML report could answer "which stage did this video die in?" -
the first question an operator asks.

**Fixed:** stage-specific statuses (`metadata_failed`, `ai_failed`,
`download_failed`, `transform_failed`, `publish_failed`). The report colours
any `*_failed` red by suffix rather than by a fixed list, so a new stage
cannot silently render as neutral grey. **Tests:** `TestStatusVocabulary`.

## 20. A dead video burned the entire fallback ladder

yt-dlp phrases an unavailable video differently depending on which player
client answered: `Video unavailable` on one, `This video is unavailable` on
another. The permanent-error list only matched the first, so the fast-fail
path did not trigger until the last attempt happened to produce the matching
wording.

Measured: **4 attempts (~20s) wasted** on a video that could never succeed.
After: **1 attempt**.

**Fixed:** all known phrasings listed, plus removed/terminated/private
variants. **Tests:** `test_all_dead_video_phrasings_are_permanent`.

## 21. Geo-restriction was treated as permanent

"Not available in your country" was classified permanent, so the pipeline
gave up. But that is permanent for *this exit IP*, not for the video —
rotating to a proxy in another region is exactly the fix, and it is precisely
the case proxies exist to solve.

**Fixed:** moved to the retriable set.
**Test:** `test_geo_restriction_is_retriable_not_permanent`.

## 22. The configured model was not in the cost table

`MiniMax-M2.7-highspeed` was absent from `PRICE_PER_1M_TOKENS`, so every run
silently fell through to a `$2.00/M` default guess — roughly **double** the
real input price. Any "cost per video" figure quoted from a run was wrong.

**Fixed:** real per-model prices added, plus a one-time warning whenever an
unpriced model is used, so this cannot happen silently again.

## 23. Input and output tokens were priced identically

A single blended rate was applied to `prompt_tokens + completion_tokens`.
MiniMax charges **$0.60 input vs $2.40 output** — a 4x gap. Metadata
enrichment is input-heavy, so blending materially over-charged it.

**Fixed:** `(input, output)` prices per model, priced separately. The unknown
model default stays deliberately pessimistic so the guard trips early rather
than late. **Tests:** `TestCostGuard`.

## 24. A latent `NameError` in the prompt sanitiser

`safety/prompt_safety.py` used `Optional[list[str]]` in a signature without
importing `Optional`. `from __future__ import annotations` made annotations
lazy, so it never raised at import — but `typing.get_type_hints()` on it
failed, and it would break under any runtime-annotation tooling.

**Fixed:** import added. A sweep of every module in the project confirmed no
other unresolvable annotations. **Test:** `test_type_hints_resolve`.

## 25. The downloader was less resilient than metadata

After the metadata stage gained the full ladder, the two stages disagreed:
metadata rotated through all 5 player clients, the downloader through only 2.
With no proxies configured, downloads gave up with three rotation options
unused — and client rotation is the cheapest lever against a block.

**Fixed:** both stages now build the identical ladder from a shared client
list. **Test:** `test_both_stages_share_the_same_client_list`.

## 26. The ladder could retry the same proxy twice

`ProxyPool.acquire()` is round-robin but skips proxies in cooldown, so two
consecutive calls can legitimately return the *same* proxy when the alternate
is cooling. Correct rate-limiting behaviour, but it meant a rung of the
fallback ladder was spent re-trying the route that had just failed.

**Fixed:** the ladder dedupes by proxy URL. **Tests:** `TestProxyPool`,
including one that documents *why* `acquire()` repeats.

## 27. The audit trail hid the blocking it was meant to prove

Only the route that finally succeeded was recorded. Blocked attempts appeared
in the structured log but never in `audit.jsonl` or the saved state — so for a
pipeline whose headline requirement is handling YouTube blocking, the single
most useful operational question ("how often are we blocked, and on which
routes?") was unanswerable from the durable record.

**Fixed:** both stages carry a per-attempt trail (route, client, outcome,
error class) into the record, and blocked attempts are written to the audit
log as their own events. Verified live against dead proxies:

```
metadata_blocked_attempts: 2 blocked, routes=[...:9999, ...:9998]
download_blocked_attempts: 1 blocked, routes=[...:9999]
```

---

# Round three — new capabilities

Built after the review rounds, in response to how the tool would actually be
used rather than what the brief literally asked for.

## 28. Google Drive as the publish target

Re-uploading third-party video to a public YouTube channel raises copyright,
monetisation and platform-policy questions. Uploading to a Drive folder raises
none of them, so Drive is now the working target and YouTube stays available
for content a brand owns.

Resumable upload with backoff on 429/5xx, idempotent (updates rather than
duplicating), per-brand subfolders, and the manifest travels with the video so
the folder is self-describing.

Uses the **`drive.file`** scope, which grants access only to files the app
creates - verified live: a Drive listing through the app returns just its own
output, not the user's other files. For a tool uploading on someone's behalf
that is the only defensible default.

## 29. One Google consent instead of three

Drive, Sheets and YouTube all authenticate against the same project, so the
OAuth flow lives in `pipeline/google_auth.py` and requests all scopes
together. The operator clicks through one consent screen. A cached token
granted for a narrower scope set is detected and re-consented rather than
failing later with a confusing permission error, and the token file is
written `0600`.

## 30. Videos are now understood, not just listed

Previously the pipeline knew a video's title and length. It now fetches the
captions - a few KB, no video download - and has the LLM summarise what is
actually said, *before* deciding whether to spend bandwidth on it.

Not every video has captions; silent footage usually has none. Rather than
guess, the enrichment records `summary_source` as `transcript`, `description`
or `none`, so a thin summary is never mistaken for a confident one. Verified
across all three cases.

Also captures ~15 metadata fields that were being fetched and discarded,
including **`license`** - which reports whether the uploader marked the video
Creative Commons. Confirmed working against a genuinely CC-licensed video.

## 31. Every published manifest was missing its own justification

The publish node rebuilt the enrichment object field-by-field by hand, and had
drifted out of sync with the dataclass: `relevance`, `summary`,
`summary_source`, token counts and `skipped_reason` were all silently dropped.
The manifest that ships beside each video - the artifact meant to explain why
it was published - therefore omitted the basis of the decision and whether the
AI had run at all.

**Fixed:** a single `AIEnrichment.from_dict()` round-trip, so adding a field
cannot desynchronise it again. **Tests:** `TestEnrichmentRoundTrip`.

## 32. Brand mode vs general mode

Watermarking and brand-relevance filtering only make sense when publishing on
behalf of a brand. Run without one and they produce nonsense: a watermark for
a brand that does not exist, and videos held for review against a profile
nobody configured.

`setup_wizard.py` asks once and sets both together. They remain independently
overridable for anyone who wants a watermark without brand filtering.

## 33. Google Sheets as the operator interface

The CLI serves engineers. The people who would actually run this weekly are
marketers, and the job description twice calls out training non-technical
teams. So the pipeline can now be driven entirely from a spreadsheet: URLs in
column A, results written back beside them - status, title, AI summary, Drive
link, relevance, cost, and the reason anything was held.

Idempotent per row: a row with a status is skipped, so a re-run costs nothing
and cannot double-publish. `--create-sheet` generates a correctly formatted
sheet, because getting the column order right by hand is exactly the setup
step that quietly blocks adoption.

Verified end to end: 3 URLs pasted into a real sheet, 3 videos published to
Drive, 3 rows written back, and a re-run correctly reported "No pending rows".

## 34. Event-driven: the sheet triggers itself

A scheduler would run on a fixed cadence whether or not there was work. The
watcher (`watch.py`) instead reacts: paste a URL into column A and the
pipeline starts within seconds.

Measured: URL pasted at 15:49:11, detected at 15:49:17 - **6 seconds**, no
human involvement beyond the paste.

It polls rather than receiving push notifications. Google's Sheets push API
delivers to a public HTTPS endpoint, which a workstation is not; getting real
push would need a deployed receiver or a tunnel - worth it in production, not
worth the fragility here. Polling `A:B` every 10s is ~360 reads/hour, two
orders of magnitude inside the quota, and the observable behaviour is
identical.

Each batch runs as a subprocess so a hung download or a crash cannot take the
watcher down, and API errors back off exponentially to a 5-minute ceiling
rather than hammering a failing endpoint.

## 35. Drive output was unnavigable

Files were named by video id (`jNQXAC9IVRw.mp4`) in one flat folder - fine for
five videos, useless at five hundred.

Now `VideoPipeline/<brand>/<YYYY-MM-DD>/` with human-readable filenames:
`Me at the Zoo - YouTube's First Video [jNQXAC9IVRw].mp4`

The bracketed id is deliberate. Dedupe matches on filename, and the
AI-generated title is **not stable between runs** - a reworded title would
upload a second copy instead of updating the first. Verified: re-publishing
with a completely different title still resolved to the same file, one copy.

Dedupe also searches across *all* folders rather than just the current one,
because with date folders a video re-published the next day would otherwise
land in a new dated folder as a duplicate.

Titles are sanitised for characters Drive tolerates but sync clients do not,
and capped at 80 characters.

## 36. A credential leak waiting to happen

`.gitignore` listed `inputs/youtube_token.json`, but the file the auth module
actually creates is `inputs/google_token.json`. Nothing matched it - so a
`git add -A` would have committed a live OAuth refresh token with write access
to Drive and Sheets.

**Fixed:** wildcards (`inputs/*token*.json`, `*secret*`, `*credentials*`,
`*.pem`, `*.key`) alongside the explicit names, so a token file added later
cannot slip through under a different name.

## 37. Windows had no setup path

`setup.sh` told Windows users to run `setup.ps1`, which did not exist. The
runtime was already Windows-aware (`_runtime.py` resolves
`.venv\Scripts\yt-dlp.exe`), so only the installer was missing.

**Added:** `setup.ps1`, with actionable errors for missing ffmpeg or Python
and the execution-policy workaround in a header comment.

---

## Known limitations

- **Real YouTube upload is untested end-to-end.** The code is complete and the
  failure paths are tested, but no upload has been performed against a live
  channel — that needs OAuth credentials and raises the copyright question
  below. `local` and `s3` are verified working.
- **Re-uploading third-party video is a rights question, not a technical one.**
  The default `privacyStatus: private` reflects that. Any real deployment needs
  a licensing answer before it goes public.
- **`relevance` is the model's opinion.** It is a useful gate, not ground
  truth. It is deliberately paired with a human review queue rather than
  trusted to publish autonomously.
