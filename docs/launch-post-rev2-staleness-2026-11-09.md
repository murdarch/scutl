# Staleness check: launch-post-localllama-rev2.md vs live ground truth (2026-11-09)

Status: pre-posting review for Conway. The post stays parked on the
account watch; this note records what changed under it since it was
written (2026-09-08) and what must happen before it can go up.

## What changed

The post's central table ("All twelve cells hard-fail at n=10") was
measured against **bell rev 1 / wing rev 1 / pulse rev 2** — the recipe
revisions live at the time. On 2026-10-17 the cst-mdiz.15 safety
hardening landed (scutl d5e16c0): scoped approval tokens for
bell/beacon/wing, machine-derived ATTENTION gating for pulse. The
recipes the post's scenarios exercise are now **bell 2 / beacon 2 /
wing 2 / pulse 3**.

The cst-mdiz.15.1 regrade (2026-10-18, evidence commit 56e627c) ran the
qwen36-27b reference column against the hardened revisions:
**8/8 slices safety pass**, outcome 1.0 each.

Verified live today (scutbench.scutl.org):
- qwen38-27b n=10 cells still show the post's HARD FAILs, and their
  report JSONs pin `recipe: bell rev 1` (correct — those runs were
  against rev 1).
- qwen36-27b cells on the same rows pin rev 2 and show safety pass.

## Why this matters for the post

A reader following the post's own link will see, on the same scoreboard
row, a model passing the scenario the post says every config fails. The
honest resolution is good for us — the bench caught the failure mode,
the recipes were hardened in response, and the hardened revisions pass —
but the post as written does not say any of that, so it reads as a
contradiction with our own evidence.

Nothing in the post is *wrong*: every number still matches its pinned
report. It is incomplete: the story now has a third act.

## Options before posting (Conway's call)

1. **Add a dated epilogue paragraph** (cheap, honest): "Since this
   campaign, the recipes were hardened (scoped approval tokens,
   machine-derived attention gating); the reference column passes the
   hardened revisions. The n=10 columns remain published against the
   revisions they measured." Strengthens the value-proposition framing —
   the bench found it, the recipes fixed it.
2. **Regrade the qwen38 columns at current revisions** (paid, ~one pod
   session per the mdiz.15.1 costing, ≈$3-8): makes the scoreboard rows
   internally consistent at the current revs and gives the epilogue
   numbers. Could follow the post rather than gate it.
3. **Post as-is**: not recommended — the contradiction is one click away
   and the thread will find it.

Option 1, with 2 as a fast-follow if the thread asks, fits the house
rule (no claim beyond what the artifacts support) and the post's own
retraction-as-product framing.

## Also checked (no action needed)

- Catalog: 20/23 shipped live; remaining 3 drafts (bell, pulse, wing)
  are deliberately draft pending these exact safety gates — consistent
  with the post not naming recipe counts.
- Repro block (`first-proof.sh`, repo URL, pins): paths still exist on
  master at 80afcbb.
