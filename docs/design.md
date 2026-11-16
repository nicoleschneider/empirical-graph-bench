# Design rules

Every UI change follows these rules. A change that breaks one is not done.

## Visual

### Color

Neutral greys plus one accent. The accent marks the current step and
highlights only. It is never decoration, never a button fill by default,
and never a chart series.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#fcfcfb` | `#1a1a19` | Page and chart surface |
| `--bg-subtle` | `#f4f4f2` | `#232321` | Table header, selected row |
| `--rule` | `#e4e3df` | `#33332f` | Hairlines, table rules, grid |
| `--rule-strong` | `#c9c8c2` | `#4a4a45` | Input borders, axis lines |
| `--text` | `#0b0b0b` | `#ffffff` | Primary text and numbers |
| `--text-secondary` | `#52514e` | `#c3c2b7` | Labels, units, captions |
| `--text-muted` | `#8a8984` | `#8f8e86` | Disabled, placeholders |
| `--accent` | `#1c5cab` | `#6da7ec` | Current step, focus ring, highlights |

Two color sets sit outside the grey and accent rule, because their meaning
needs color: status and chart series. Both are fixed below.

### Not allowed

Gradients, glows, shadows beyond a 1px hairline, emoji, illustrations, hero
sections, icons used as decoration, background images, animated
transitions longer than 150 ms.

### Type

- IBM Plex Sans for text. IBM Plex Mono for numbers, ids, commands and
  hashes. Never Inter.
- Fonts are self-hosted from `@fontsource/ibm-plex-sans` and
  `@fontsource/ibm-plex-mono`. No font CDN.
- Sizes: 13px body, 12px table cells and labels, 15px section titles,
  18px page title. Weights 400 and 600 only.
- `font-variant-numeric: tabular-nums` on every element that shows numbers.

### Numbers

- Right-aligned in tables, in Plex Mono, with tabular numerals.
- Every value carries its unit: `4.90 s`, `87 %`, `15.2 ×`, `1.4e-17`,
  `25,447,873 edges`. A unitless metric (modularity, NMI) is named in its
  column header.
- Precision: times to 3 significant figures; ratios to 2 decimals; shares
  as whole percentages; modularity and NMI to 4 decimals; tolerances and
  L1 distances in scientific notation with 2 significant figures.
- Medians are the default statistic. Ranges and intervals sit next to the
  median, never in a tooltip only: `0.0561 s [0.85, 1.11]`.

### Layout

- Tables and charts first. No cards, no tiles holding a single number
  unless that number is the screen's answer.
- Corner radius 2px. Rules 1px. No boxes around sections; sections are
  separated by 32px of space and a heading.
- Padding inside controls: 4px vertical, 8px horizontal. Table rows 28px.
- One column of content up to 1200px wide. Tables scroll horizontally
  inside their own container, never the page.

### Status

Status is symbol plus color plus a word, never color alone.

| Status | Symbol | Color | Word |
|---|---|---|---|
| Prediction met | ✓ | `#0ca30c` | Met |
| Prediction refuted | ✗ | `#d03b3b` | Refuted |
| Statistical tie | ≈ | `#fab219` | Tie |
| Pending | · | `--text-muted` | Pending |

The word stays in `--text`. Only the symbol takes the status color.

### Charts

- Each configuration gets one color for the whole session, assigned when
  the configuration is added and stored in `config.json`. Every chart,
  legend and table swatch uses it. A filter never repaints a series.
- Series colors, in assignment order:

  | Slot | Light | Dark |
  |---|---|---|
  | 1 | `#2a78d6` | `#3987e5` |
  | 2 | `#eb6834` | `#d95926` |
  | 3 | `#1baf7a` | `#199e70` |
  | 4 | `#eda100` | `#c98500` |
  | 5 | `#e87ba4` | `#d55181` |
  | 6 | `#008300` | `#008300` |
  | 7 | `#4a3aa7` | `#9085e9` |
  | 8 | `#e34948` | `#e66767` |

  Slots 1 to 7 pass the colorblind and contrast checks in both modes as
  adjacent series. Slots 3, 4 and 5 are below 3:1 on the light surface, so
  every chart has direct labels or a table view.
- Scatter and dot charts with overlapping marks show at most 3
  configurations per panel. More than 3 become small multiples.
- A ninth configuration is not a new color. It folds into small multiples.
- One y-axis per chart. Two measures become two charts.
- Log scales for time across graph sizes, with labeled ticks in seconds.
- Lines 2px, markers at least 8px, grid and axes in `--rule`.
- Labels, values and legends are in text tokens, never in series colors.
- Every chart has a legend for two or more series and a table view of the
  same data.
- Emphasis inside a chart is done by muting the other series, not with the
  accent.

## Copy

- Labels: one to three words. "Run experiments", "ETL share", "Base seed".
- Sentences: at most about 12 words. Most screens need one or two.
- State facts with numbers, not descriptions.
  - Yes: "ETL: 87% of GPU time."
  - No: "Loading takes a large share of the GPU's time."
- No marketing or filler words: powerful, seamless, unlock, effortlessly,
  let's, simply, dive.
- No em dashes. No exclamation marks.
- Empty states give one instruction: "Run the initial experiments."
- Errors say what failed and the next action: "Dataset hash mismatch.
  Download web-Stanford again."
- Buttons are verbs: "Record", "Run", "Import". Not "Submit" or "OK".
- Sentence case everywhere. No title case, no all caps.

## Review checklist

Before a UI change is done, check:

- [ ] Only tokens from this file; no new colors.
- [ ] Accent appears only on the current step or a highlight.
- [ ] Every number is mono, tabular, right-aligned and has a unit.
- [ ] Status uses symbol, color and word.
- [ ] Each configuration keeps its stored color in every chart.
- [ ] No banned words, em dashes or exclamation marks in any string.
- [ ] Empty states are one instruction.
- [ ] Works in light and dark mode.
