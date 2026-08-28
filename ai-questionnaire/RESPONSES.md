# Component C - AI Questionnaire

> **Status:** Q7 and Q8 are complete - they are answered from this build and
> need nothing further. Q1-Q6 and Q9 are about *your* experience; each is
> drafted with structure plus the parts this project genuinely evidences, and
> every gap is marked **`[NEEDS YOUR INPUT]`**. Do not submit those sections
> as-is - a fabricated work history is the easiest thing for a reference check
> to catch.

---

## Section 1 - AI Usage Baseline

### Q1. Hours per week using AI tools for work

**`[NEEDS YOUR INPUT]`** - choose one: 0-30 min / 30-120 min / 2-5 hrs / 5-10 hrs / 10+ hrs

Answer honestly. A high number is only credible if Q2-Q4 show matching depth.

---

### Q2. AI tools and platforms you actively use

*Grounded in this project - safe to use verbatim:*

| Tool | Where it sits | Level of involvement |
|---|---|---|
| **MiniMax (M2.7-highspeed)** | Metadata enrichment + brand-safety classification in the video pipeline | Built with. Integrated via the OpenAI-compatible SDK, including structured JSON output, per-call cost accounting against a spend guard, and a parser that strips `<think>` reasoning blocks. |
| **LangGraph** | Orchestration agent - stateful graph over load → metadata → AI → download → transform → publish | Built with. Designed the node graph, conditional routing, circuit breaker, and checkpoint/resume. |
| **yt-dlp** | Acquisition layer, wrapped in a proxy/player-client fallback ladder | Built with. Wrote the retry ladder, block-vs-permanent error classification, and throttling. |
| **FFmpeg** | Transform stage - re-encode, metadata strip, scale, watermark | Built with. Including a Pillow+`overlay` watermark path for ffmpeg builds without libfreetype. |
| **Claude Code** | Development environment | Built with. |

**`[NEEDS YOUR INPUT]`** - add anything else you genuinely use (ChatGPT,
Cursor, Midjourney, Zapier, n8n...). Be precise in the third column:
"configured a Zap" and "built a multi-step Zap with custom code and error
branches" are read very differently.

---

## Section 2 - AI Fluency & Judgment

### Q3. An AI automation you personally built or redesigned

Needs: the problem, tools, inputs/outputs, and a **measurable outcome**.

**`[NEEDS YOUR INPUT]`** - a real project of yours. Notes to gather:
- What was manual before, and who did it?
- How long per instance, how often?
- What did you build, with which named tools?
- **The number** - hours saved, throughput, error reduction. An estimate you
  can defend beats a vague claim.

*If you have no prior example*, the packet explicitly allows a cross-functional
one, and this pipeline qualifies - but frame it accurately as a project build,
not a deployed production system.

---

### Q4. Most complex automation deployed in a live environment

Needs: triggers, data flow, failure handling, monitoring, and **explicitly what
you built vs. what engineering/IT built**.

**`[NEEDS YOUR INPUT]`** - the real one. That last clause is the point of the
question; be scrupulous.

*Architecture vocabulary from this build, if useful for structuring the answer:*
- **Trigger:** CLI / scheduled invocation with a URL allowlist
- **Data flow:** load → validate → metadata → AI enrich + safety → download →
  transform → publish, with typed state at every boundary
- **Failure handling:** three-class error taxonomy (permanent / blocked /
  unknown); proxy and player-client rotation with jittered backoff; circuit
  breaker after N consecutive failures; graceful degradation at every stage
- **Recovery:** idempotent re-runs, checkpoint/resume from saved state
- **Monitoring:** JSONL audit log, per-video cost ledger, self-contained HTML
  report, non-zero exit when nothing was published

---

## Section 3 - Cross-functional Communication

### Q5. Translating a non-technical team's manual process into automation

**`[NEEDS YOUR INPUT]`** - a real example. Prompts to jog it:
- Whose process, and how did you learn it was painful?
- Did you *watch* them do it, or work from a description? (Watching is the
  stronger answer - people under-describe their own exceptions.)
- What did you ask about edge cases? That is where automation projects die.
- How did you show the result back - demo, doc, pilot on real data?
- What did you get wrong first, and how did you find out?

> A strong instinct to include if it is true for you: ask *"what do you do when
> it goes wrong?"* rather than *"what do you do?"* People narrate the happy path
> from memory and the exceptions only when prompted - and the exceptions are
> most of the actual work.

---

### Q6. Handling pushback or skepticism about AI tools

**`[NEEDS YOUR INPUT]`** - a specific instance if you have one.

> The strongest version concedes the skepticism was *partly right*. Someone
> burned by a tool that quietly produced wrong output is not being irrational.
>
> A concrete hook from this build: during development, the AI layer generated
> a confident, on-brand title - *"Cooking Basics: Getting Started in the
> Kitchen"* - for a video that was actually a man at a zoo, and published it
> while its own reasoning field said the content was unsuitable. That is
> exactly what skeptics fear, and it is why the pipeline now has a relevance
> gate that holds uncertain content for a human. Showing people the guardrail
> converts more skeptics than showing them the output.

---

## Section 4 - Governance & Risk

### Q7. What parts of a workflow do you intentionally keep human-only, and why?

*Complete - answered from this build.*

Three categories, and in this pipeline all three are enforced in code rather
than in policy.

**1. Irreversible public actions.** Re-publishing a third party's video under
a brand's account is public, hard to walk back, and carries copyright and
reputational risk that no model confidence score justifies. So the YouTube
publisher defaults to `privacyStatus: "private"` - making something public is
a deliberate human decision, never a default. The pipeline does all the work
and then stops.

**2. Judgment calls the model is not actually qualified to make.** I am
comfortable letting an LLM *draft* metadata and *flag* concerns. I am not
comfortable letting it decide, unsupervised, that someone else's content
belongs on a brand's channel. The relevance gate encodes this: content scoring
below the brand's threshold is marked `held_for_review` and is not published,
even when it is entirely harmless.

This distinction was not theoretical. The safety classifier and the metadata
generator were answering two different questions - *"is this harmful?"* and
*"does this fit the brand?"* - and only the first gated publication. Beach
drone footage is not harmful, so it sailed through and was published with an
invented cooking-themed title. The fix was to separate relevance from harm and
gate on both.

**3. Anything where a rare error costs far more than the automation saves.**
This is the general principle behind the other two. Automating a task that is
right 97% of the time is excellent when the 3% costs a re-run, and reckless
when the 3% is a copyright strike or a brand's audience seeing content it
never approved.

What I *do* fully automate: acquisition, transformation, format QC, cost
accounting, and audit logging - all stages whose failure mode is a wasted run
rather than a public mistake.

A related design position: **the human step must be cheap for the human.** A
review gate that is annoying gets routed around within a fortnight. Held items
keep their artifacts and audit trail on disk so a reviewer can act on them
later without re-running anything.

---

### Q8. How do you prevent AI from introducing errors in production workflows?

*Complete - answered from this build.*

Four layers, each addressing a different failure mode.

**Hallucination - never let the model be the only check on a verifiable
claim.** The LLM scores relevance and drafts metadata; deterministic rules
independently check the content, and the model's verdict cannot override a
rules-based rejection. Three opinions are merged - rules, the safety
classifier, and the metadata call's own judgement - and the most conservative
wins.

Concretely: the model once returned the literal string `"reject"` in the
`title` field, and the video was published titled "reject" while its verdict
read "safe". Two defects, both real. Titles are now validated against a set of
control tokens and fall back to the original title, and the metadata call's
`brand_safety` verdict - which was being parsed and then silently discarded -
is now folded into the decision.

**Structure - make malformed output survivable.** Structured output is
requested via `response_format`, but that is not sufficient on its own:
reasoning models emit `<think>` blocks containing example JSON, which naive
first-brace/last-brace extraction happily parses instead of the real answer.
The extractor strips reasoning wrappers, scans for a brace-balanced object
while respecting string literals, and falls back to `json_repair` for
unescaped quotes. When parsing genuinely fails, the run degrades to
rules-only rather than crashing - and *records that it did*.

**Data handling - least privilege and no silent scope creep.** Credentials
live in `.env` (gitignored, alongside OAuth tokens and client secrets), never
in brand config. URLs are validated against an allowlist rather than a
denylist. Filenames derived from untrusted metadata are sanitized against path
traversal. Prompt inputs are sanitized before reaching the model.

**Monitoring and rollback - assume it will go wrong and make that cheap.**
- A JSONL audit log records every decision with its reason.
- Per-video cost is tracked as a *delta*, not the guard's running total - a
  subtle bug that made a five-video run report a triangular over-count.
- A spend guard hard-stops LLM calls at a configured ceiling, because "the
  automation quietly spent the budget overnight" is an error too.
- Re-runs are idempotent and resumable from checkpoint.
- Nothing reaches a public destination without passing both gates.

The thread through all four: **make failure visible and cheap rather than
trying to make it impossible.** The most dangerous property this pipeline had
was not that things broke - it was that they broke while reporting
`Failed: 0`. Silent degradation is how a pipeline quietly gets worse for months
without anyone noticing.

---

## Section 5 - Staying Current

### Q9. How do you stay current on AI developments?

Needs **named** sources and how you turn learning into production changes.

**`[NEEDS YOUR INPUT]`** - the specific ones you actually read. Generic answers
score poorly against a rubric that explicitly rewards tool specificity. Name
people, newsletters, communities, vendor changelogs, papers, events.

> The second half of the question is the part most candidates skip. Have a
> concrete learning → production example.
>
> One from this build if you want it: reading MiniMax's API docs closely
> surfaced that `MiniMax-Text-01` rejects `response_format={"type":
> "json_object"}` outright while M2.7 accepts it, and that the international
> endpoint is `api.minimax.io` rather than `api.minimax.chat`. Both were
> failing *silently* behind graceful degradation - the pipeline reported
> success while the AI layer had never once executed. Vendor docs and
> changelogs are underrated: they change more often than the models do, and
> they change your architecture.
