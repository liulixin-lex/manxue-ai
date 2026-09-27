# Independent result surfaces and moving-scene prompts

Implemented 2026-09-26. The existing public green identity remains; the admin surface keeps its own stylesheet. This is an operational observatory, not a marketing landing page.

## Information architecture

- **Candy** is the default tab: current node, current scoring rules, last 24 hours, one square per actual request. Red means request error, yellow means an incorrect final answer (the requested label is “降智”), green means passed. Icons, text, keyboard focus and the detail dialog supplement color. No empty time bins count as results.
- **Pelican** has its own three-column desktop / two-column tablet / one-column mobile gallery. The image leads, followed by time, pelican verdict, scene, model, node and review state. Filters query the pelican test's status, never the overall run or candy verdict. All-node gallery scope and current-node statistics are explicitly labeled.
- **Guest** separates the form from recent results, and separates candy squares from pelican previews. Guest pelicans remain labeled as basic validation without visual review.
- Tab selection uses shareable hashes. Arrow keys, Home and End operate the tabs. Old run links still open, and ?run=ID&test=candy opens the candy detail only.
- Review evidence and raw outputs remain in the existing detail dialog. Short native disclosures keep long explanations out of the primary reading path.

## Public design tokens

Stylesheet: app/web/observatory.css. Shared admin styles are not changed.

| Token | Light | Dark |
| --- | --- | --- |
| Page | #f6f7f7 | #151d19 |
| Surface | #ffffff | #1b2520 |
| Primary text | #20322b | #e4ece7 |
| Secondary text | #607068 | #a0b4a7 |
| Border | #dce3df | #35463b |
| Brand accent | #216f50 | #83cfa8 |

System sans-serif fonts preserve Chinese readability without network font requests. Main text is 14px, headings 23–30px, metadata 12px. Spacing uses a 4px base; content is capped at 1280px with 40/28/20/16px responsive gutters. Panels are 10px radius, buttons/fields 6px, statuses 4px. Status squares are 30px desktop / 28px mobile with 6–7px gaps. Semantic square colors remain red #ce3b40, yellow #edbd40, green #23845f in both themes. Image canvases retain a neutral light base so changing the application theme does not modify generated artwork.

## Motion and stability

GSAP 3.15.0 is pinned, integrity-verified and served locally. Only user-triggered tab, dialog and filter transitions animate (200ms, 6px translate and opacity). Polling does not animate results. Interrupted transitions are killed and their inline styles cleared. Reduced-motion preference, preference changes and hidden pages are handled. Without GSAP, all functionality and content remain available.

API requests time out after 20 seconds. A failed sync keeps existing data and displays an explicit stale-data message. Unchanged results do not replace the DOM; gallery focus survives refreshed records. Polling pauses in background tabs and resumes when visible.

## Prompt version 2

The existing SVG-only format and subject-based review policy v4 remain. New generation combines 16 scene-specific motion descriptions, 6 visual styles and 4 riding actions. Scheduled/manual site runs avoid repeating the same node's last three scenes. The exact selected prompt is persisted with the run; old prompts and artwork are not rewritten.

Every new prompt asks for a side-tracking view: the bird rides right, road/foreground and distant environment move left at different speeds. Ground motion and at least one environment layer must visibly move; static speed lines or shaking only the whole SVG are insufficient. Scene-specific cues include passing fence posts, drifting hills, moving riverbanks, blowing leaves or rotating windmills. A 4-second base loop with 1/2-second wheel/pedal subcycles keeps common loop sampling predictable. Repeated tiles should cover the viewport at both loop boundaries. The nonce stays fixed on screen.

These are generation requirements, not added automatic rejection criteria. A prompt cannot guarantee model compliance; inspect the actual output and saved frames. Background motion does not reintroduce a strict visual veto or automatically invalidate recognizable subjects. Guest requests use the same generator prompt builder; candy scoring and review retry budgets are unchanged.

## References inspected

As of 2026-09-26:

- https://pelicanzoo.ai/ — artwork-first catalog, model metadata and direct sources. Borrowed separation of artifact and supporting metadata, not the site's decorative styling.
- https://pelicans.jetty.bot/ — distinct benchmark views, inspectable trajectories and keyboard navigation. Borrowed explicit mode separation.
- https://codex-pulse.com/ — large side-by-side artwork and separate gallery filters. Borrowed image scale and progressive detail.
- https://oneshotlm.com/r/pelican-on-bicycle-z-ai-glm-5/ — the 2026-07-22 prompt asks for an inline SVG illustration; its generated description claimed motion while the published assessment recorded a static result. This motivates explicit, observable motion instructions rather than trusting output descriptions.
- https://github.com/AzatJalilov/PelicanSdf/blob/main/benchmark/PROMPT.md — explicit subject, bicycle and animation requirements. Its WebGL/JavaScript execution format is not used by this SVG-only application.
- https://gsap.com/docs/v3/GSAP/gsap.matchMedia()/ — reduced-motion and cleanup guidance.

The implementations and Chinese prompt text are original to this project. References are design/methodology inspiration, not evidence that a single test identifies a model or its overall quality.

## Verification

Tests in tests/test_result_views.py cover per-test SQL filters, pagination, legacy handling, independent statistics, prompt diversity, scene non-repetition and persisted prompts. The browser case checks RGB colors, square geometry, separated details, keyboard tabs, 320/390/768/1024/1440px overflow, reduced motion, interrupted transitions, request failure/recovery and operation with the animation library blocked. The prior end-to-end evidence test still covers real Chromium rendering, saved screenshots, review details and the admin route. Visual captures additionally inspect real public records at desktop/mobile sizes and both color schemes.


## 2026-09-26 · Compact unified overview

The public overview now places candy metrics and its three-row history above the pelican gallery. Guest testing keeps a separate view. The first six pelicans appear initially; “展示更多” adds six at a time and preserves existing image elements. Gallery refreshes fetch at most four pages concurrently.

Candy results use clean 16 px solid squares inside 24 px touch targets, read down three rows then across in time order. The finite viewport keeps the newest completed results and drops the oldest when a new result arrives. The final gray slot represents the current candy request; while idle its accessible label says it is waiting for the next request (or paused). Empty outlined slots never count as results. Arrow keys navigate the grid; status remains available through accessible labels and details. Reduced-motion settings disable transitions.

Public copy no longer includes scoring/review/prompt version badges, implementation descriptions, raw request JSON or renderer/browser metadata. Stored records and API evidence remain available. Existing prompt version markers are omitted from the display without editing stored history.

Angular surfaces use offset soft shadows, tighter corners, hover/press feedback, a short status conveyor and progressive gallery transitions. The illustration itself stays untransformed for inspection. The shared admin stylesheet is unchanged.

New prompts retain the pelican/bicycle/riding subject, moving near/far layers, automatic four-second seamless loop, fixed text nonce and safe standalone SVG output. Scene/style/action diversity is retained. Prompts are about 220–240 Chinese characters, with no version label. Review policy and candy scoring are unchanged.
