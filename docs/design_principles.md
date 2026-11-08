# Design principles

Rules every Schemalab view follows. Each rule names its source and the place in the product it applies to. When a rule and a deadline disagree, the rule wins and the feature waits.

## Sources read (2026-11-07)

| Source | What was read | Notes |
|---|---|---|
| Wilke, *Fundamentals of Data Visualization* | Chapters on redundant coding, color pitfalls, proportional ink, uncertainty (clauswilke.com/dataviz) | The GitHub repo is chapter source only; the rendered book was read instead |
| Alexander, *Telling Stories with Data* | Book front page and chapter plan | Workflow and reproducibility framing |
| Distill, "How to Use t-SNE Effectively" | Full article | The model for explorable views |
| Bremer, Visual Cinnamon portfolio | Project index and categories | The index has no prose about method; principles below come from the project categories and stated course aims |
| FlowingData | Not read | flowingdata.com returned HTTP 403 to automated fetches. The annotation rules below come from the brief, not the site |

## 1. Never present one run as the truth (Distill)

The t-SNE article shows the same data at several perplexities and several random seeds side by side, and shows that a plot taken too early looks "pinched". A reader learns the result is a function of its settings.

In Schemalab:
- **Transaction lab:** the isolation level is a control. The reader switches it and watches the lost update appear or disappear on the same schedule.
- **Benchmarks:** each query runs at least 5 times. The page shows every run as a dot, not one bar. Scale factor is a control.
- **Eval table:** accuracy is shown per model and per agent version, with the number of questions next to it. A score from one seed is labeled as one seed.
- **Agent answers:** the confidence label says how it was derived (critic passed, retried, empty result), not just "high".
- Every view with parameters has a shareable URL that encodes its state.

## 2. Honest axes (Wilke, proportional ink)

- Bars on a linear axis start at 0. No exceptions.
- Bars on a log axis start at 1 and are used only for ratios ("DuckDB is 40× faster"). Amounts on a log axis are dots.
- A non-zero baseline is allowed only for a change chart, where the bar runs from 0 to the change.
- Dots and lines may use a tight range, since they encode position, not length.

## 3. Direct labels over legends (Wilke, redundant coding)

- Label lines at their end and points next to the point. A legend is the last resort.
- If a legend is unavoidable, order it as the data is ordered visually, never alphabetically.
- Do not explain colors in a caption sentence; make the marks self-identifying.
- Fill in a light tint, outline and label in a darker shade of the same hue.

## 4. Color that survives color-vision deficiency (Wilke, color pitfalls)

- Categorical: Okabe–Ito, at most 5 hues in one view. Beyond about 8 categories, stop coloring and label directly.
- Sequential: one hue, monotonic in lightness. No rainbow scales.
- Diverging: a CVD-safe pair (PiYG-style), never red against green.
- Never rely on color alone. States like "blocked", "lock held" or "rolled back" also get a shape or text.
- No large saturated fills; saturation is reserved for the one mark the takeaway is about.
- Each palette is checked in a CVD simulator before it ships.

## 5. Distributions as distributions (Wilke, uncertainty)

- Show every observation when there are few (benchmark runs, eval seeds): strip or dot plots.
- Many observations (profiling a column): histogram or density, with the median marked, not a mean alone.
- Intervals always say what they are ("95% CI", "min–max of 5 runs").
- To compare two groups, show the interval of the difference. Overlapping error bars are not a test.
- Probabilities for a lay reader (A/B test, forecast): quantile dotplots or frequency framing (12 of 100), not a bare percentage.
- No bar-plus-error-bar charts.

## 6. Lead with the takeaway (brief: FlowingData style)

- Every view opens with one sentence that states the finding in plain words, for example "Under READ COMMITTED, T2's write is lost." It is computed from the data, so it changes when the controls change.
- The chart title is the takeaway; the axis labels carry the units.
- Annotations sit on the chart next to the mark they explain, with a thin leader line, not in a side box.

## 7. Bespoke, data-dense forms (brief: Visual Cinnamon)

The portfolio files a whole category of work as "Bespoke", next to beeswarms, flow diagrams, packing and radial layouts. Default chart types are the start of a design, not the end.

In Schemalab, the main views get a form designed for their data:
- **Transaction lab:** a two-lane timeline (one lane per session), statements as marks on a shared time axis, lock waits as gaps, the anomaly as an annotated link between the two lanes.
- **Schedule checker:** the schedule as a grid (transactions × time) with the precedence graph drawn beside it; a cycle is highlighted on both.
- **Normalization:** FD closure shown as a step list where each new attribute is tagged with the FD that added it.
- **Benchmarks:** a beeswarm per query and engine on a log time axis.
- **Query plans:** an icicle tree where width is rows and shade is time.

## 8. Typography and hierarchy (all sources)

- One serif for prose and takeaways, one sans for UI, one mono for SQL. Sizes come from a fixed scale.
- Numbers in tables use tabular figures and are right-aligned.
- Reading text is limited to about 70 characters per line.
- Hierarchy comes from size and weight, not from boxes, shadows or gradients.

## 9. Reproducible by default (Telling Stories with Data)

The book's workflow is plan, simulate, acquire, model, communicate, and it is prescriptive about reproducibility. In Schemalab:
- Every answer shows the exact SQL or code that produced it and the tables it read.
- Every benchmark records hardware, engine versions and scale factor next to its results.
- Every chart can be exported as a notebook that reruns it.
- Write-ups say what was done and what was not, including results that did not hold.

## Checklist for a new view

- [ ] Opens with a computed takeaway sentence
- [ ] Has at least one control that changes the conclusion, and a shareable URL
- [ ] Shows distributions or all runs, not a single number
- [ ] Bars start at 0 (or 1 on a log axis)
- [ ] Direct labels; any legend is in visual order
- [ ] Okabe–Ito or single-hue palette, checked under CVD simulation, with no meaning carried by color alone
- [ ] Uses a form chosen for the data, not a default chart picked first
- [ ] Shows its provenance (SQL, tables, settings)
