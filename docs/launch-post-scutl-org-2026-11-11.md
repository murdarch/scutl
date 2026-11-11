# Paste-ready: scutl.org megathread comment (short form, per OP guidance — Conway 2026-11-11)

Status: DRAFT for Conway. Third revision: cut to ~220 words against
the megathread OP's three questions (plain-english what/why,
comparison, validation evidence) and the thread's actual comment
length (1-3 paragraphs). The thread runs an AI-detector bot
(Stylome) that publicly flags AI-written comments, so the honest-AI
disclosure line is load-bearing twice over. Voice: public-voice
house guide (brain/doc/public-voice.md, cst-grn7), relaxed a notch.
Prior long form preserved in git history (7c9217c) as reply/blog
material.

Facts checked against the repo: 22 of 25 recipes `status: shipped`;
the three drafts are the three the n=10 safety campaign hard-failed.
("a year in" cut per Conway 2026-11-11: two pivots make any age
claim misleading; "License: MIT" spelled out so nobody reads it as
an institute affiliation.)

----- BEGIN COMMENT -----

https://github.com/murdarch/scutl — catalog: https://scutl.org

**What it is:** recipes that let a local model do real-world things safely — hold a wallet (testnet or mainnet USDC), rent a VPS or GPU, run a website, sell a paid service, manage email, backups, secrets rotation. 22 shipped so far. Each recipe pairs a small task-specific CLI, where the limits (spend caps, allowlists, approval gates, teardown) are enforced in code before any call leaves the box, with a skill doc the model reads for the loop and the judgment calls. The dangerous action isn't refused at runtime; it's inexpressible, because the flag doesn't exist. Install is one instruction: point your agent at the recipe's ADAPT.md and it installs itself.

**vs the usual options:** guardrails as prompt text, or a general API wrapper plus hope. We benchmarked prompt-level safety to death first: every local model we tested takes a social-engineering lure some of the time — 30–70% per cell at 10 seeds, per-seed transcripts public at https://scutbench.scutl.org. So authority lives in the CLI and judgment stays with the model.

**Validation:** every Shipped badge links its run receipts (graded reports, pinned environments, ssh-signed checksums), and a recipe doesn't ship until a fresh agent with no project history completes the install doc unaided. The three recipes that hard-failed our own safety bench are still marked draft — the gate eats its own cooking. Two-minute local proof, nothing real attached: `./tools/first-proof.sh http://localhost:8080`

License: MIT. Researched and written by AI agents with human review — the project is partly an existence proof of its own pitch.

----- END COMMENT -----

## Notes (not part of the post)

- Structured on the OP's three numbered questions, labels made ours.
- The Stylome bot will likely flag this anyway; the closing line
  converts that from gotcha to confirmation.
- Long form (git 7c9217c) remains the source for a scutl.org blog
  post or thread replies; the n=3→n=10 story likewise
  (launch-megathread-comment-2026-11-11.md).
- "statically typed" stays out per the 2026-11-11 repo check
  (annotated Python, no mypy/pyright/py.typed); the claimed static
  property is the argument grammar.
