# Build log

This repository is the build record. The dates and commits below are the real ones, in order. Nothing here was reconstructed after the fact.

The suite was built on 2026-11-18 and 2026-11-19 with AI coding agents writing most of the code. Product decisions, prompts, guardrails, evaluations, review, and QA were mine. The rounds below show the build, then the review passes that came from using the demo and fixing what did not work.

| Date | Round | What changed | Commits |
|---|---|---|---|
| 2026-11-18 | Dataset | Synthetic transition book with protocol, consent, and signature fields. Follow-ups added statement cues and the account-based value model. | [4752d1a](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/4752d1a), [4dd62fa](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/4dd62fa), [a544d9e](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/a544d9e) |
| 2026-11-18 | Pipeline | Account-based value model, protocol consent gate, and signature load. | [c201f6e](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/c201f6e) |
| 2026-11-18 | Web app | Six screens, guided tour, approvals, audit trail, value model, and evaluation page. | [7af8c48](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/7af8c48) |
| 2026-11-18 | Suite platform | DeepSeek client with recorded runs, shared audit, hub, and the generated method page. | [b4f0461](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/b4f0461), [84ec0b7](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/84ec0b7) |
| 2026-11-18 | Five samples, one at a time | Supervision, Client Concierge, Book IQ, then LP Ops, each on the same platform and client record. | [d8c74b4](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/d8c74b4), [55dccdf](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/55dccdf), [f1e5147](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/f1e5147), [eac85f1](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/eac85f1) |
| 2026-11-18 | Method and docs | Method page reads all five evaluations. Suite README, deploy config, and portfolio brief. | [763352d](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/763352d), [d2dbde2](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/d2dbde2) |
| 2026-11-19 | Presentation review | Suite nav on the Transition pages, disclaimer pinned under the menu, build story added, brief reframed. | [5cd5b7a](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/5cd5b7a), [3f2e1dd](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/3f2e1dd) |
| 2026-11-19 | Focus layout | One-record cards for Concierge, Supervision, and LP Ops, borrowed from the Savvy Intelligence product video. | [33b5b60](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/33b5b60) |
| 2026-11-19 | Usability pass | Animated expansion, then a multi-open no-jump version. Book IQ detail moved inline under its row and filter chips update in place. | [302e95f](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/302e95f), [d06845a](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/d06845a), [28a6920](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/28a6920) |
| 2026-11-19 | Live-call hardening | The live ranking call was spending its token budget on reasoning and returning nothing. Raised the budget and restored the previous recording when a call fails to parse. | [b15c650](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/b15c650) |
| 2026-11-19 | Transition interactions | Overview claim cards open at the top. Intake source documents and Plan filters switch in place instead of reloading and jumping. | [91df311](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/91df311) |
| 2026-11-19 | LP Ops interactions | Packet tabs and stage filters switch in place, and the clicked control stays at the same viewport position. | [5486d57](https://github.com/anthonystachowitz-wq/savvy-product-samples/commit/5486d57) |
| 2026-11-19 | Build log | This file, plus the final anchor fix for LP Ops. | current commit |

## Why the review rounds matter

The first build was usable, but not calm. Reviewing it on a live screen exposed the things a build cannot: links that jumped to anchors, cards that expanded so fast they looked like a reload, filters that scrolled the page, and a live model call that returned nothing when it used all its tokens on reasoning. Each round above fixed one of those, and the tests stayed green: 179 passing at the end, with 54 recorded model calls and the evaluations and failures left visible on the module pages and on /method.

The point of showing this is not that the first version was perfect. It is that the work was reviewed, measured, and corrected in public.

