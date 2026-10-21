# How we tuned it: adapting Strands Decider to a domain without training

VoiceShop sends every spoken sentence through Strands Decider 2B. The decider answers typed questions (who is this for, which category, which item on screen) with calibrated per-option probabilities. Out of the box it already chose sensibly, but our first question design and a flat confidence threshold made it look unsure about obvious things. This page records how we fixed that **without changing the model's weights**, and how the same method applies to any other domain.

**Which catalog.** Steps 1 and 2 and the pointing results were measured on 2026-10-18 on VoiceShop's earlier fashion catalog (11 categories). VoiceShop now ships a generated catalog with 10 departments and 50 categories ([ADR-27](architecture-decisions.md#adr-27-synthetic-catalog)). The method carries over unchanged. The thresholds in step 3 were recalibrated on 2026-10-19 on the synthetic catalog's labeled set, `backend/agentcore-runtime/eval/catalog` (116 scenarios, 221 turns, split into tuning and held-out). The earlier numbers stay here as the historical record.

## What "domain adaptation without training" means

The model stays the same. Two things around it change:

1. **Question design**: which questions are asked, how their options are named and described, and what goes into the state text.
2. **Threshold calibration**: per-question confidence thresholds chosen from measured accuracy on a labeled set, instead of one number for everything.

This is not a persona. A persona is a prompt instruction for a generative model ("you are a shopping assistant"). The decider does not generate text and has no voice. Here we change the *questions it is asked* and *how much we trust each kind of answer*.

## Why it was needed

The decider's confidences are calibrated: they are honest about uncertainty, so a correct answer at 0.6–0.7 is normal. The model card's own example routes a payments complaint to "billing" at confidence 0.659. Our design started with one act threshold of 0.8 for every question. On real sentences, that meant:

- "a gift for my mom" committed nothing: the recipient `parent` was at 0.56, and for gender the decider preferred `not_mentioned` (0.48) over `women` (0.41).
- "the second one" was the right item at only 0.46.
- "yes, add it to my cart" was `confirm` at 0.48.

## Step 1: build a labeled set for the domain

The first labeled set, for the earlier catalog, had 86 shopper sentences with gold answers for every question the session host asks. They cover:

- recipients with and without an implied gender;
- stated gender;
- categories, styles and vague budgets;
- pointing by position and by description;
- confirmations, checkout, undo, start over, product questions;
- combined sentences.

A fixed 12-item screen was used for pointing. A script sent every sentence to the deployed endpoint. Those files belonged to the earlier catalog and have been removed. The current set is `eval/catalog/scenarios.jsonl`: each scenario is a short session of one or more turns with gold answers. `eval/catalog/run_catalog_eval.py run` plays every scenario against the deployed session host and records every decider answer with its full probabilities. `run_catalog_eval.py score` then reports, for each question and each candidate threshold:

- **precision**: how often a committed answer is right;
- **recall**: how many sentences that state a value get it committed;
- the lowest threshold that reaches the target precision (95%).

## Step 2: redesign the questions

We compared three designs on the same set (top-1 accuracy, 2026-10-18, ml.g6.xlarge):

| Question | v0: as first built | v1 | v2 / production |
|---|---|---|---|
| intent | 0.94 | 0.92 | **0.95** |
| recipient | 0.93 | 0.99 | **0.99** |
| gender | 0.83 | 1.00 | **1.00** |
| category | 0.84 | 0.54 | **0.87** |
| pointing | 0.70 | 0.90 | **0.90** |

The changes that mattered:

- **Describe every option.** The intent options became short descriptions with examples, for instance `pointing`: "Picks out one specific item that is already on screen, by its position or as 'that one'". With bare option names, most intents scored below 0.5. With descriptions, "yes, add it to my cart" went from 0.48 to 0.94.
- **Let the chosen option carry the meaning.** Instead of a coarse `parent` plus a separate gender question, recipients are fine-grained: `mother`, `father`, `parent` (not saying which), `wife`, `husband`, `daughter`, `son`, and so on. The decider picks `mother` reliably, and `mother` implies women's products. This maps the decider's own answer to a schema, it is not a keyword rule. Gender went from 0.83 to 1.00.
- **Say positions the way people do.** Pointing labels changed from "row 1 center, item 2" to "Item 2, the second item: row 1, second from the left". The pointing round's state text also lists the numbered shelf. Pointing went from 0.70 to 0.90.
- **What did not work: separate position questions.** On a real shelf of long, similar titles, the item question put "the second one" on the wrong card. We added three questions in the same call: which number, which row and which side the shopper says. On the sentences we tuned with, they looked better. On a held-out set they made things worse: 12 of 40 opened right, against 27 for the item question alone. The decider doesn't answer "which number did the shopper say" literally. For "the dog watch" it answers the dog watch's number in the listing, so the extra questions were a second item question, not a separate signal. We removed them. See "Pointing: confirm by the on-screen number" below and [ADR-28](architecture-decisions.md#adr-28-pointing-by-on-screen-number).
- **Give each question only the context it needs.** v1 also put the shelf listing into the understand round, and that pulled category and intent toward whatever was on screen: category dropped to 0.54, and "a wallet" was read as pointing. v2 sends the listing only to the pointing question.

## Step 3: calibrate thresholds per question

`agent/calibration.json` holds the act and ask thresholds per question. `policy.Thresholds.from_env()` reads it. Each value is the lowest act threshold whose commit precision is at least 95% on the tuning split, with every higher threshold also at least 95%, confirmed on the held-out split. Precision / recall below are tuning / held-out on `eval/catalog` (2026-10-19) unless noted:

| Question | Act | Precision / recall at that threshold |
|---|---|---|
| intent | 0.50 | 0.98 / 0.70 on the earlier catalog; re-checked on the synthetic catalog: actions other than narrowing were right 11 of 11 (tuning) and 4 of 4 (held-out) |
| recipient | 0.50 | 1.00 / 0.94 on the earlier catalog; synthetic catalog: precision 1.00 on both splits, recall 0.86 / 0.93 |
| gender | 0.50 | 1.00 / 0.96 on the earlier catalog (mostly implied by the recipient) |
| department | 0.40 | precision 1.00 on both splits, recall 0.79 / 0.73; no wrong commit at any threshold |
| category | 0.55 | precision 1.00 on both splits, recall 0.86 / 0.86; asked within a department (about 5 options). The old 0.85 gave recall 0.46 |
| budget_band | 0.65 | 1.00 / 0.17 on the earlier catalog (explicit amounts are parsed in code) |
| pointed_item | 0.20 | 1.00 / 0.90 on the earlier catalog's tuning set; 84% of opens right on held-out real shelves (see "Pointing" below) |
| offer_reply | 0.65 | precision 1.00 on both splits, recall 0.66 / 0.69 |
| cart_item | 0.85 | **provisional** (2026-10-20): 6 removal sentences on the live endpoint; below act the host asks "Should I remove ...?" |
| noMatch | 0.35 | `none_of_these` at or above it means no match: 2 of 61 fitting requests flagged and 11 of 13 out-of-catalog ones caught (tuning); 0 of 26 and 5 of 6 (held-out). The earlier 0.135 flagged 14 of 61 |

### Pointing: confirm by the on-screen number

Every card on the shelf shows its number. Pointing uses one policy around the item question:

- **At or above 0.20:** open the card. Opening is reversible, and the spoken line names the card by number: "Opened No. 3, …, $19.99. Should I add it to your cart?"
- **0.10 to 0.20:** ask "Do you mean No. 5, the silver dog-print kids' watch?", spoken even when spoken replies are off. "Yes" opens it.
- **Below 0.10:** ask "Which number do you mean? You can also tap it."

The 0.20 threshold comes from the tuning set: the lowest with open precision ≥ 0.95. The tuning set was 36 cases, on a hand-made shelf and a shelf from the earlier catalog, run 3 times.

The held-out set was 40 sentences written after the policy was frozen, on a shelf of 12 women's watches from the earlier catalog, 8 with near-identical titles. It was never used to choose a threshold. Results over 3 runs (the pointing script, shelves and result files belonged to the earlier catalog and were removed with it):

| Outcome | Count |
|---|---|
| Opened the right card | 27 |
| Opened a wrong card | 5 |
| "Do you mean No. N…?" with the right number | 2 |
| "Do you mean No. N…?" with a wrong number | 2 |
| "Which number?" | 4 |

So 84% of opens were right on held-out data, against 95% on the tuning set: the tuning set was easier than a real shelf. The wrong opens are row-relative phrases such as "the one at the bottom right" and "the second from the left in the bottom row". Plain numbers and descriptions work. This is the clearest case for fine-tuning (below). Until then, a wrong open costs the shopper one "no, number 12".

## Step 4: policy rules that fit the confidences

These design decisions were made on 2026-10-18 and 2026-10-19:

- **Narrowing is the safe default.** When the top intent is narrowing, at any confidence, the turn narrows and each slot commits on its own confidence. Chips can be removed with a click.
- **Irreversible intents need act-level confidence.** Below their threshold, `confirm`, `go_back` and `start_over` become an offer: the action is recorded, one yes/no question is asked ("Should I go back to the previous results?"), and the shelf is left alone. `place_order` is always an offer, at any confidence: "Should I place the order?". An empty cart is reported instead. The question is spoken even when spoken replies are off. The reply is its own question, asked with only the offer and the reply: a confident yes carries out the recorded action, a confident no drops it (ADR-31).
- **An offer answers only the next sentence.** If the shopper says something else, the offer is dropped, so a stale "add it to your cart?" never survives a new search.
- **Every chip is re-asked each turn.** Each committed chip's question is asked again with only the latest sentence and the current chips, without the earlier sentences, so they do not pull the answer back. The recipient is re-asked without its own chip in the context. A confident different answer replaces the chip ("actually it's for my dad" changes Mom to Dad). An unsure one becomes an offer: "Should I shop for your father instead?". A new recipient resets the gender, department, category and budget ([ADR-33](architecture-decisions.md#adr-33-change-of-mind-by-re-checking-every-chip)).
- **Unspecified relatives get the obvious follow-up.** For a parent, partner or child with no gender, the next question is "Is that your mom or your dad?".
- **Exact numbers stay in code.** "Under fifty dollars" is parsed, never asked of the model.
- **No-match keeps the closest items.** When `best_fit` says none of the candidates fits, the closest items stay on screen, marked tentative, and one yes/no offer drops the filter that adds the most matches ([ADR-29](architecture-decisions.md#adr-29-no-match-keeps-the-closest-items-and-offers-to-widen)).
- **Ask every open slot, and ask intent on its own.** Every open slot is asked in each round (a slot left out was never heard). Intent is asked in its own parallel call without the earlier sentences, so earlier context does not pull it. Remembered preferences from earlier visits stay out of the understand round: they pulled "a gift for my dad" from father 0.79 to 0.47. They are still used for ranking, hints and Sonic.

## Result

The scripted conversation through the real Gateway and decider:

1. "a gift for my mom under eighty dollars" commits For: Mom, Women, Under $80.
2. "actually it is for my dad" changes them to Dad, Men.
3. "show me watches instead" adds Watches.
4. "make it under fifty dollars" changes the budget to Under $50.
5. "the second one" opens the item.
6. "yes, add it" adds it to the cart.
7. "place my order" asks "Should I place the order?", and "yes" places it.
8. "something for my parent" asks "Is that your mom or your dad?".

Known gap: row-relative pointing ("the one at the bottom right") opens the wrong card in about 1 of 8 held-out sentences; the shopper corrects it by number.

## Applying this to your domain

1. **List the decisions** your agent makes per turn as typed questions (`choice`, `noul`, `score`).
2. **Write a labeled set:** 50–100 realistic inputs with gold answers, including indirect phrasings and near-misses.
3. **Describe every option** in the user's own words, with an example where it helps.
4. **Make options carry meaning:** prefer fine-grained options whose choice implies the follow-on fact, over asking the model to infer it separately.
5. **Hold out real data:** keep a held-out set on realistic data, written before you look at results, and report on it only. VoiceShop's hand-made test shelf hid the pointing problem, and a design that looked better on the tuning set failed on held-out sentences.
6. **Scope the context:** give each question only the state it needs.
7. **Let the catalog define the options:** read the option lists and their descriptions from the catalog (VoiceShop reads departments, categories and descriptions from `CATALOG#META`), so a new catalog needs no code change. It does need a new labeled set and a recalibration.
8. **Ask hierarchical questions when the options grow:** a 50-way question spreads probability thin. VoiceShop asks the department first (10 options), then the category within it (about 5), in the same sentence when the department commits ([ADR-30](architecture-decisions.md#adr-30-department-then-category-questions)).
9. **Calibrate per question:** pick the lowest act threshold that meets the precision your product needs. A wrong action costs more than a clarifying question, so irreversible actions get a stricter bar.
10. **Re-run the evaluation** whenever questions, options, the catalog or the model change, and commit the thresholds with the numbers that justify them.

The same loop fits support-ticket triage (team, urgency, product area), restaurant ordering (item, size, modifiers), travel booking (destination, dates, class) or IT helpdesk routing.

**Keeping it valid as the catalog changes.** Calibration is measured against a catalog. When products are added, the same evaluations run as a gate before new items go live, and thresholds are only changed by a reviewed recalibration, never automatically. See [Catalog updates in production](catalog-updates-in-production.md) (ADR-25).

**When to fine-tune instead.** Fine-tuning is the next lever when a question's precision–recall curve on your labeled set cannot reach the bar at any threshold after redesign. The strands-decider repository publishes the training recipe for its LoRA adapter. In VoiceShop, no question needed it.
