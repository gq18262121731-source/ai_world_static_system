**Comparison Target**

- Source visual truth: `C:\Users\Test1\AppData\Local\Temp\codex-clipboard-914b0146-7b2e-4ec5-982f-d9628f48024e.png`, together with the user's instruction that the highlighted region must place vital signs first and health score below.
- Implementation: `http://localhost:4173/#/elders/1`
- Implementation screenshot: Codex in-app Browser tab 1 capture, 2026-09-09 20:47 CST (session-local authenticated browser capture; no filesystem path is exposed by the browser surface).
- Viewport: 1908 × 882 CSS px.
- Source pixels: 1908 × 882 at 96 DPI; implementation CSS size: 1908 × 882; device pixel ratio: 1.0. No density normalization was required.
- State: administrator logged in, elder 张三, health score loaded at 84 (良), 近 24 小时 selected.

**Full-view Comparison Evidence**

- The current-vital cards appear first: 心率、血氧、体温、血压.
- The four simultaneous trend charts follow the cards in a two-column grid, and every caption retains “每 6 分钟一个点”.
- Trend notes and the compact sleep/steps/battery row remain directly after the charts.
- The complete health-score section now follows those vital-sign elements inside the same `.vitals-card`; it is no longer placed above the charts.
- The authenticated browser capture shows the bottom pair of vital-sign charts, trend notes, compact status row, and the beginning of the health-score section in one continuous view, confirming the requested vertical order.

**Focused Region Comparison Evidence**

- DOM child order was checked as `card-title → vitals → trend-head → trend-grid → notes → extra → score-section`.
- `.trend-grid` precedes `.score-section`, `.score-section` remains inside `.vitals-card`, and only one health-score section is present.
- The score ring, change badge, summary, deduction list, confidence copy, and seven dimension cards remain intact after the move.
- No new raster assets were required; existing icons and visual assets were preserved.

**Findings**

- No actionable P0/P1/P2 visual differences remain.
- P3: the combined card is intentionally tall because it contains four charts and the complete score breakdown. The section divider and heading preserve a clear reading sequence.

**Required Fidelity Surfaces**

- Fonts and typography: existing application font stack, weights, line height, hierarchy, wrapping, and small-label treatment are unchanged; no visible clipping was introduced.
- Spacing and layout rhythm: the existing card padding, chart grid, section gap, radii, and dividers are preserved; only the section order changed.
- Colors and visual tokens: existing surface, border, teal brand, semantic progress, warning, danger, and muted text tokens are reused without drift.
- Image quality and asset fidelity: existing source assets and icon components remain unchanged; no placeholders or code-drawn substitutes were added.
- Copy and content: the full health-score copy is preserved, all four vital-sign labels remain correct, and the chart cadence consistently reads every six minutes.

**Comparison History**

- Earlier layout: health score appeared before the vital-sign charts inside the highlighted region.
- Fix made: moved the complete `.score-section` after the trend grid, trend notes, and compact status row without changing its data or presentation.
- Post-fix evidence: the 1908 × 882 authenticated browser capture and DOM-order check both show vital signs before health score.
- This iteration produced no remaining P0/P1/P2 finding, so no additional blocking visual-fix loop was required.

**Primary Interactions Tested**

- Confirmed all four trend charts render together for the 近 24 小时 selection.
- Confirmed the chart cadence remains every six minutes.
- Confirmed the browser console contains no errors or warnings.
- Confirmed the page build completes successfully; only the pre-existing Vite chunk-size advisory remains.

**Implementation Checklist**

- [x] Keep current-vital cards at the start of the combined content area.
- [x] Keep all four six-minute trend charts immediately after the cards.
- [x] Move the complete health-score section below all vital-sign content.
- [x] Preserve score data, styling, chart data, cadence, and responsive behavior.
- [x] Verify rendered order, browser console, and production build.

final result: passed
