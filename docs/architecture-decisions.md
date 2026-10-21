# Architecture Decision Records

Each record gives the context, the options considered, the decision, its consequences, and the evidence in [research.md](research.md). Status is **Accepted** (settled), **Proposed** (waiting on a measurement or check, or documented but not built) or **Superseded** (replaced by a later record, kept for the history). Open facts are marked **TO VERIFY**. Records are never deleted: a later decision adds a dated amendment or a status change.

| # | Decision | Status |
|---|---|---|
| [ADR-1](#adr-1-decision-model) | Strands Decider 2B as the decision model | Accepted |
| [ADR-2](#adr-2-decider-hosting) | Decider on AgentCore Runtime Instances with a GPU | Superseded by ADR-22; VPC amendment superseded by ADR-26 |
| [ADR-3](#adr-3-session-host) | AgentCore Runtime microVM with WebSocket as the session host | Accepted |
| [ADR-4](#adr-4-voice-model) | Amazon Nova 2.5 Sonic for speech in and out | Accepted |
| [ADR-5](#adr-5-catalog-data) | Amazon Reviews 2023 (Amazon_Fashion) as the catalog | Superseded by ADR-27 |
| [ADR-6](#adr-6-filtering) | Hard rules in code; the model only classifies and ranks | Accepted |
| [ADR-7](#adr-7-confidence-thresholds-and-clarifying-questions) | Act ≥ 0.8, ask < 0.5, narrow in between | Accepted (amended: per-question thresholds; orders always offered first) |
| [ADR-8](#adr-8-retrieval) | OpenSearch Serverless vector + metadata filters | Accepted; private access restored by ADR-32 |
| [ADR-9](#adr-9-memory) | AgentCore Memory for preferences | Accepted |
| [ADR-10](#adr-10-how-the-session-host-calls-the-decider) | Session host calls the decider directly (now SageMaker InvokeEndpoint) | Accepted (amended 2026-10-19) |
| [ADR-11](#adr-11-frontend) | React + Vite on Amplify Hosting | Accepted |
| [ADR-12](#adr-12-gateway-target-type) | Gateway with Lambda targets, no API Gateway | Accepted (amended 2026-10-20: five tools) |
| [ADR-13](#adr-13-product-image-delivery) | Product images through presigned S3 URLs | Accepted |
| [ADR-14](#adr-14-decider-container-architecture) | x86_64 CUDA image for the decider | Accepted; S3-pulled venv and EBS volume superseded by ADR-22 |
| [ADR-15](#adr-15-who-decides-and-who-speaks) | Code and the decider decide; Sonic speaks a grounded outcome | Accepted |
| [ADR-16](#adr-16-decider-warm-pool) | Fixed warm pool of decider session ids | Superseded by ADR-22 |
| [ADR-17](#adr-17-cart-and-orders-as-separate-tables) | Separate `VoiceShop-Cart` and `VoiceShop-Orders` tables, with a `place_order` tool | Accepted (amended 2026-10-19: transactional writes) |
| [ADR-18](#adr-18-gender-as-a-typed-slot) | Gender/demographic as a typed-question slot with the same act/ask/narrow bands as every other slot | Accepted |
| [ADR-19](#adr-19-pre-curated-catalog-asset) | Pre-curate the catalog once as a versioned release asset instead of a live dataset download per deploy | Superseded by ADR-27 |
| [ADR-20](#adr-20-catalog-eligibility-from-the-items-own-text) | Read color, material and gender from the item's own title/features when `details` lacks them; size optional; rebuild the search index on each curation run | Superseded by ADR-27 (index rebuild kept) |
| [ADR-21](#adr-21-no_match-from-an-explicit-none-of-these-option) | `no_match` from an explicit `none_of_these` option in `best_fit`; product picks are ranked, never shown with a confidence | Accepted (what a no-match shows: ADR-29) |
| [ADR-22](#adr-22-decider-on-a-sagemaker-real-time-endpoint) | Decider on a SageMaker real-time GPU endpoint with auto-scaling, replacing AgentCore Runtime Instances | Accepted |
| [ADR-23](#adr-23-visual-first-spoken-replies-optional) | Visual-first: Sonic always listens, spoken replies off by default behind a toggle | Accepted |
| [ADR-24](#adr-24-speculative-retrieval-on-partial-transcripts) | Start catalog search on stable partial transcripts; re-budget first UI to 1.1 s | Accepted |
| [ADR-25](#adr-25-catalog-updates-checked-with-the-decider-before-publishing) | Production catalog updates: image model fills the metadata schema, decider checks gate publishing; thresholds never retuned automatically | Proposed (not implemented) |
| [ADR-26](#adr-26-no-vpc) | No VPC: IAM-only access to OpenSearch Serverless, Lambdas outside any VPC, decider with network isolation | Search part superseded by ADR-32; decider part accepted |
| [ADR-27](#adr-27-synthetic-catalog) | A generated (synthetic) catalog that ships in the repository, loaded by one CodeBuild job | Accepted |
| [ADR-28](#adr-28-pointing-by-on-screen-number) | Pointing: the item question alone, cards numbered on screen, unsure picks confirmed by number | Accepted |
| [ADR-29](#adr-29-no-match-keeps-the-closest-items-and-offers-to-widen) | No-match keeps the closest items and offers to widen | Accepted |
| [ADR-30](#adr-30-department-then-category-questions) | Department question first; category asked only within a department | Accepted (thresholds measured 2026-10-19) |
| [ADR-31](#adr-31-offer-replies-screen-actions-and-orders) | The reply to an offer is its own question; screen actions are intents; an order is always offered first | Accepted (offer_reply thresholds measured 2026-10-19) |
| [ADR-32](#adr-32-private-search-collection) | Search collection reachable only through a VPC endpoint; the two search functions in a one-zone VPC; index writes through an index-writer function | Accepted |
| [ADR-33](#adr-33-change-of-mind-by-re-checking-every-chip) | Every chip is re-asked with only the latest sentence; a confident different answer replaces it, an unsure one becomes a yes/no question | Accepted |
| [ADR-34](#adr-34-more-results-cart-removal-and-yesno-answers) | Show more, remove from the cart, Yes/No buttons, voice-off reply text, chunked shelf frames, and two reply fixes | Accepted |

---

## ADR-1: Decision model

**Status:** Accepted

**Context.** Each sentence needs a few classification-style answers (who it is for, which category, budget band, what the shopper is doing, which on-screen item they mean), each with a confidence we can trust. The confidence drives whether the assistant acts or asks, which is the main point of the demo. The answers are on the latency path of every sentence.

**Options considered.**

| Option | For | Against |
|---|---|---|
| Strands Decider 2B | Apache-2.0; from Strands Labs (AWS); calibrated per-option probabilities; ~115 ms median per question on a consumer GPU; questions on the same state are cheap | New; serving schema and AgentCore pairing not yet measured |
| OpenJev | Same model family idea; good published latency on H100 FP8 | Weights are CC BY-NC 4.0 (non-commercial), a poor fit for an AWS sample people copy; needs ~29 GB FP8 on large GPUs |
| Hosted Jev (TypeSafe AI) | Proven in the origin prototype | Third-party service outside AWS; data leaves the account; 0.7-1.1 s per call in the original demo overlay |
| Bedrock LLM with structured output (for example Amazon Nova Micro) | Fully managed, no GPU to run | No calibrated per-option probabilities (only the generated answer, so confidence would be self-reported); slower per decision |
| Amazon Personalize | Managed ranking | Ranks items from interaction data; cannot answer typed intent questions about a sentence |

**Decision.** Strands Decider 2B, model `strands-decider-2B-qwen3.5-v1-2610`.

**Consequences.** We run a GPU host (ADR-2, now ADR-22) and own its warm-up (ADR-16, now a warm minimum on the endpoint). We get real per-option probabilities to show on chips and cards. No non-GPU fallback is built: the sample is deployed only for development, testing and recording, and a slower, uncalibrated backend would undercut the point of the demo. Exact `/v1/systemone` schema is TO VERIFY and isolated in one client module.

**Evidence.** research.md sections [2](research.md#2-jev-and-system-one-decision-models), [3](research.md#3-strands-decider), [6](research.md#6-earlier-research-notes).

---

## ADR-2: Decider hosting

**Status:** Superseded by [ADR-22](#adr-22-decider-on-a-sagemaker-real-time-endpoint) (2026-10-18). Its networking amendment is superseded by [ADR-26](#adr-26-no-vpc) (2026-10-19). Kept for the record: the spike measurements on this hosting are in [research.md](research.md#measurements).

**Context.** The decider needs low hundreds of milliseconds per round to keep the first UI update within 1 s after the shopper stops talking. It must stay private and should not add a second compute platform if we can avoid it.

**Options considered.**

| Option | For | Against |
|---|---|---|
| AgentCore Runtime Instances with a GPU capacity provider | Same platform and API (`InvokeAgentRuntime`) as the session host; AgentCore installs GPU drivers; VPC private; g5/g6/g6e supported | Not scale-to-zero (EC2 + management fee); first call per session id provisions an instance; new feature with some behaviors TO VERIFY |
| AgentCore Runtime microVM on CPU | Scale-to-zero, simplest | Measured 7.6 s for one question and 20-23 s for three, cold start ~70 s. Far too slow |
| Amazon SageMaker real-time endpoint on GPU | Mature GPU serving | A second platform to deploy, secure and explain. The earlier research notes chose it for OpenJev-specific reasons (vLLM, FP8, per-letter scores) that do not apply to Strands Decider |
| Amazon Bedrock Custom Model Import | Fully managed | Qwen3.5 architecture not listed; logprobs only for the chosen token, so no per-option probabilities |

**Decision.** AgentCore Runtime Instances with a GPU capacity provider in private subnets.

**Consequences.** The whole backend stays on AgentCore. The GPU instance is the largest running cost, so the scripts and README explain scaling down or tearing down after each session of use. An early spike measures latency on g6.xlarge and g5.xlarge. If both miss the target, we try g6e.xlarge before any architecture change. If the capacity provider has no CDK alpha support, a provisioning script calls the raw API (project convention: no hand-rolled `CfnResource`).

**Amendment (networking, confirmed against AWS docs):** a capacity provider's configuration mandates a VPC and subnets ("Networking is VPC only" per research.md section 5, and the AgentCore console flow requires choosing a VPC when creating one). This is a hard platform requirement of the Instances compute type, not a security choice we are electing to make — unlike most samples of this kind, which have no resource that needs a VPC at all (see ADR-8's networking amendment for the "why does this project differ" question). We are NOT, however, routing those subnets to a NAT gateway: the decider's weights are baked into its container image (`HF_HUB_OFFLINE=1`), so it makes no outbound internet calls at runtime, and AgentCore's own guidance confirms a NAT gateway is only needed "to enable internet access" for a VPC-attached workload. `VoiceShop-NetworkStack` therefore provisions private subnets with S3/DynamoDB gateway endpoints and an OpenSearch Serverless VPC endpoint, and no NAT gateway, saving its ~$32/month running cost and removing a piece of infrastructure nothing in this design needs.

**Amendment (2026-10-19).** The VPC described above existed only because the Instances capacity provider requires one. ADR-22 moved the decider to SageMaker, so nothing needed the VPC any more. `VoiceShop-NetworkStack` was deleted. See [ADR-26](#adr-26-no-vpc).

**Evidence.** research.md sections [4](research.md#4-strands-decider-on-agentcore-cpu-microvms), [5](research.md#5-agentcore-runtime-instances-gpu), [6](research.md#6-earlier-research-notes); AWS docs confirming capacity providers require a VPC (`runtime-instances-how-it-works.html`, `runtime-instances-get-started-console.html`) and that private subnets without a NAT gateway have no internet route (`agentcore-vpc.html`).

---

## ADR-3: Session host

**Status:** Accepted

**Context.** One process per shopping session must hold the browser's audio connection, bridge it to Sonic, keep session state, call the decider and tools, and push UI updates.

**Options considered.**

| Option | For | Against |
|---|---|---|
| AgentCore Runtime (microVM, WebSocket on HTTP protocol) | Proven by the earlier voice ordering sample this one adapts (SigV4 WebSocket + Nova Sonic + Gateway, research.md section 9); per-session isolation; consumption pricing; Gateway and Memory fit naturally; project convention | 8-hour session cap and idle timeout (fine for shopping) |
| AWS Fargate service | Full control of the process; the first choice in the earlier research notes | We would build WebSocket auth, scaling, per-session isolation and load balancing ourselves; off the project convention |

**Decision.** AgentCore Runtime microVM. The earlier research notes listed AgentCore Runtime as an alternative.

**Consequences.** We reuse the template's auth, WebSocket client and Sonic bridge code patterns. The Sonic 8-minute connection limit applies with either option because it is a limit of the Sonic stream, so session continuation is built in the host either way (`sonic_bridge.py`).

**Evidence.** research.md sections [6](research.md#6-earlier-research-notes), [7](research.md#7-amazon-nova-25-sonic), [9](research.md#9-template-repo-voice-in-app-ordering).

---

## ADR-4: Voice model

**Status:** Accepted

**Context.** The shopper speaks and hears replies. We need final transcripts quickly and natural spoken replies, including short clarifying questions.

**Options considered.**

| Option | For | Against |
|---|---|---|
| Amazon Nova 2.5 Sonic (speech-to-speech) | One bidirectional stream for transcripts and speech; tool use; the newest Sonic model; used in the authors' newest sample | 8-minute connection limit; model ID and pricing TO VERIFY |
| Separate STT + TTS (for example Deepgram Flux + a TTS service) | Fine-grained control of end-of-turn and voices; used in the origin prototype | Two or three vendors, more hops, third-party data path; off the AWS-native story |

**Decision.** Amazon Nova 2.5 Sonic.

**Consequences.** Session continuation is a P0 requirement. Sonic's end-of-turn timing sits inside our latency budget and is measured in the spike.

**Evidence.** research.md sections [1](research.md#1-origin-the-voice-gift-finder-prototype), [7](research.md#7-amazon-nova-25-sonic).

---

## ADR-5: Catalog data

**Status:** Superseded by [ADR-27](#adr-27-synthetic-catalog) (2026-10-19). VoiceShop no longer uses this dataset. Kept for the record.

**Context.** The demo needs real products with USD prices (for budget filtering), images, brands, categories and attributes such as color.

**Options considered.**

| Option | For | Against |
|---|---|---|
| Amazon Reviews 2023, Amazon_Fashion | USD prices, images with variants, brand, categories, `details` (Color, Size, Material); 825.9K items to curate from; citable paper | Research-oriented; usage terms TO VERIFY; prices from crawl time |
| Amazon Berkeley Objects | Good images and metadata | No prices |
| Kaggle Amazon product scrapes (for example `lokeshparab/amazon-products-dataset`) | Has prices | Unclear license; prices in INR |
| Kaggle fashion product image datasets | Good images | No prices |

**Decision.** Amazon Reviews 2023, Amazon_Fashion, curated to 500-1,500 items at deploy time.

**Consequences.** A curation job filtered for valid price, image, category and brand, copied images into our bucket (no hotlinking), and wrote DynamoDB and OpenSearch. The usage terms stayed TO VERIFY. They were never confirmed, which is why ADR-27 replaced the dataset.

**Evidence.** research.md section [8](research.md#8-catalog-dataset-amazon-reviews-2023).

---

## ADR-6: Filtering

**Status:** Accepted

**Context.** Shoppers state hard limits ("under 100 dollars", "a bag"). Showing a product that breaks a stated limit destroys trust. Models are unreliable at arithmetic.

**Options considered.** (a) Let the decider or an LLM judge whether items fit the budget. (b) Apply budget, committed category and availability as deterministic rules in code, and use the model only to classify the sentence and rank allowed candidates.

**Decision.** (b). A code parser turns explicit amounts into cents. The session host applies the hard filters as the last step before anything is shown, and the OpenSearch query applies the same filters as an optimization. The model never compares prices or computes totals; cart totals are summed from integer cents in the cart Lambdas.

**Consequences.** Budget correctness is unit-testable. Vague budgets ("nothing too pricey") go to a `budget_band` question, and only a committed band becomes a filter.

**Evidence.** research.md section [6](research.md#6-earlier-research-notes) (filter-in-code step).

---

## ADR-7: Confidence thresholds and clarifying questions

**Status:** Accepted. Values configurable; per-question thresholds since 2026-10-18 (amendments below).

**Context.** The differentiator is acting when sure and asking when not. We need clear bands and a rule for what to say.

**Options considered.** (a) Always act on the top answer. (b) Always confirm before acting. (c) Two thresholds: act at ≥ 0.8, ask one short question below 0.5, keep narrowing in between without committing.

**Decision.** (c), with `ACT_THRESHOLD=0.8` and `ASK_THRESHOLD=0.5` from configuration. At most one clarifying question per sentence, naming the top two options. Destructive intents (start over, go back) and add-to-cart only run at ≥ 0.8. When no candidate is confident, the assistant says so and asks instead of presenting weak results (for 50-way product choices, see ADR-21 and ADR-29).

**Consequences.** The UI shows confidences on chips and labels sub-threshold results as tentative (cards show no confidence, ADR-21). The Strands Decider calibration claim (≥ 0.9 right about 95% of the time on unseen short tasks) supports using raw probabilities; we review real distributions in the spike before recording.

**Amendment (2026-10-18).** One threshold for every question proved too strict for calibrated answers. Each question now has its own act and ask thresholds in `backend/agentcore-runtime/agent/calibration.json`, measured on a labeled set. `ACT_THRESHOLD` and `ASK_THRESHOLD` (0.8 and 0.5) are the defaults for questions not listed there. Unsure irreversible intents (`confirm`, `place_order`, `go_back`, `start_over`) become one spoken yes/no question. See [How we tuned it](how-we-tuned-it.md).

**Amendment (2026-10-20).** Since ADR-31, `place_order` is not gated by confidence: an order is always offered first ("Should I place the order?"), at any confidence, and an empty cart is reported instead. The current thresholds per question are in `calibration.json` and [How we tuned it](how-we-tuned-it.md#step-3-calibrate-thresholds-per-question).

**Evidence.** research.md sections [3](research.md#3-strands-decider), [6](research.md#6-earlier-research-notes).

---

## ADR-8: Retrieval

**Status:** Accepted. Network access: private, through a VPC endpoint only ([ADR-32](#adr-32-private-search-collection)). The networking amendments below are the history.

**Context.** Each sentence needs candidates from up to 1,500 products, filtered by price, category and availability and ordered by semantic fit, with at most 50 passed to the decider.

**Options considered.**

| Option | For | Against |
|---|---|---|
| Amazon OpenSearch Serverless (vector + metadata filters) | kNN with filter clauses in one query; managed; IAM data access; VPC endpoint | Minimum OCU cost while the collection exists |
| Amazon Bedrock Knowledge Bases | Managed ingestion and RAG | Built for document chunks, not structured catalog rows with hard numeric filters; adds a layer we would work around |
| In-memory vectors in the session host | Fastest | Off the settled architecture; reloads per session |

**Decision.** OpenSearch Serverless `VECTORSEARCH` collection, standby replicas off, access only through a VPC endpoint (amended by ADR-26 to the public endpoint with IAM-only access; restored by ADR-32: VPC endpoint only). Embeddings from Amazon Titan Text Embeddings V2 (1,024 dimensions). Knowledge Bases stays an optional later addition (for example for store policy questions).

**Consequences.** Tool Lambdas and the curation job ran in the VPC (removed by ADR-26). Since ADR-32, only `search-catalog` and `catalog-index-writer` run in a VPC; the curation job stays outside it. OpenSearch is the second-largest running cost and is included in teardown guidance.

**Amendment (why this project has a VPC when similar samples do not).** The question came up directly: similar samples (including the one this sample adapts) provision no VPC, NetworkStack, or NAT gateway. We checked whether that absence should carry over here, and it does not, for one concrete reason: the decider's AgentCore Runtime Instances capacity provider has a mandatory VPC/subnet field (confirmed against AWS docs — see the ADR-2 amendment), which those samples do not trigger because none of them use the Instances/GPU compute type. OpenSearch Serverless's VPC-only network policy is a second, independent reason we are choosing (not required) to keep in this design, since restricting AOSS to VPC-only access is straightforward once the VPC already exists for the decider. So the VPC here is justified by an actual platform requirement, not imported by habit from a generic "private subnets are more secure" default. We are still dropping the NAT gateway (see ADR-2 amendment): nothing in the VPC needs outbound internet, so gateway/interface VPC endpoints cover S3, DynamoDB, and OpenSearch Serverless access without one.

**Amendment (2026-10-19).** Both reasons above are gone. The first (the capacity provider) went with ADR-22. The second was a choice made only because the VPC already existed. The collection's network policy now allows the public endpoint, and every request must still be SigV4-signed by a role named in a data access policy. See [ADR-26](#adr-26-no-vpc).

**Amendment (2026-10-20).** The 2026-10-19 amendment no longer describes the current system. [ADR-32](#adr-32-private-search-collection) made the collection private again: requests come only through its VPC endpoint, for a production reason (no public network path to the search index), not because of the decider.

**Evidence.** AWS docs confirming AgentCore Runtime Instances capacity providers require a VPC.

---

## ADR-9: Memory

**Status:** Accepted

**Context.** Shoppers share preferences ("mom likes purple, bags") that should carry across turns and, for a returning shopper, across sessions.

**Options considered.** (a) AgentCore Memory with the user preference strategy. (b) A hand-rolled DynamoDB preference table.

**Decision.** (a). Session state in the current session stays in host memory, with snapshots in DynamoDB. Preferences go to AgentCore Memory keyed by Cognito `sub` and are loaded at session start as "Remembered" hints, never auto-committed as chips.

**Consequences.** Long-term extraction is asynchronous, so preferences appear in later sessions. Memory is never a hard dependency: any failure logs a warning and the session continues without it (project convention).

**Evidence.** Project convention (AgentCore Memory pattern); research.md section [9](research.md#9-template-repo-voice-in-app-ordering).

---

## ADR-10: How the session host calls the decider

**Status:** Accepted

**Context.** The decider is called on every sentence, often twice. Gateway is the project's layer for tools. Gateway targets are Lambda, OpenAPI, Smithy or MCP-server style.

**Options considered.**

| Option | For | Against |
|---|---|---|
| Direct `InvokeAgentRuntime` from the host (boto3, SigV4) | One hop; no MCP framing; typed request passes unchanged; IAM scoped to the decider ARN | Not discoverable as an MCP tool (not needed: code calls it, not the LLM) |
| Through AgentCore Gateway | Uniform tool layer | Needs a Lambda target that itself calls `InvokeAgentRuntime` (two hops) or an MCP server wrapper around the decider; extra latency on the critical path every sentence; typed questions are not tools an LLM should choose |

**Decision.** Direct `InvokeAgentRuntime`, one module-level client, no SDK retries, 3 s read timeout, calls off the event loop.

**Consequences.** Gateway remains for catalog and cart tools. The decider wire format lives in one client module.

**Amendment (2026-10-19).** Since ADR-22 the decider is a SageMaker real-time endpoint, so the host calls `sagemaker-runtime` `InvokeEndpoint` on `voiceshop-decider` instead of `InvokeAgentRuntime`. The rest holds: one hop, one module-level client in `decider_client.py`, no SDK retries, calls off the event loop, IAM scoped to the endpoint ARN. Measured InvokeEndpoint overhead in-Region is 8.8 ms p50 ([research.md](research.md#sagemaker-endpoint-adr-22-2026-10-18)).

**Evidence.** research.md section [5](research.md#5-agentcore-runtime-instances-gpu) (same invoke API for Instances).

---

## ADR-11: Frontend

**Status:** Accepted

**Context.** A browser page with a product wall, chips, product detail, cart, debug overlay, mic capture at 16 kHz and playback at 24 kHz.

**Options considered.** (a) React + Vite + TypeScript on Amplify Hosting, adapted from the template. (b) Plain HTML/JS (an older pattern). (c) Another framework.

**Decision.** (a).

**Consequences.** Reuse the template's Cognito sign-in and SigV4 WebSocket presigning. Amplify hosting is created by a sibling CDK app.

**Evidence.** research.md section [9](research.md#9-template-repo-voice-in-app-ordering); project convention.

---

## ADR-12: Gateway target type

**Status:** Accepted

**Context.** The template exposed API Gateway + Lambda endpoints through Gateway. Here the tools are called only by the session host through Gateway, and `search_catalog` is on the per-sentence path.

**Options considered.** (a) API Gateway REST + Lambda behind an OpenAPI target (template). (b) Lambda targets directly.

**Decision.** (b). Four Node.js 22.x Lambdas as Gateway Lambda targets with `AWS_IAM` inbound auth.

**Consequences.** One fewer hop and fewer resources. No public REST API exists. Tool input validation lives in the Lambdas.

**Amendment (2026-10-20).** There are now five tool Lambdas: `search_catalog`, `get_product_detail`, `add_to_cart`, `remove_from_cart` (deletes one cart line in DynamoDB, ADR-34) and `place_order`. `VoiceShop-CatalogIndexWriter` (ADR-32) is a sixth function, invoked at deploy time and not a Gateway tool.

**Evidence.** Latency budget: `search_catalog` is on the per-sentence path ([research.md](research.md#measurements)).

---

## ADR-13: Product image delivery

**Status:** Accepted

**Context.** Images must come from our bucket, not external hosts. The dataset's usage terms were TO VERIFY, so we should not republish images openly.

**Options considered.** (a) Public bucket. (b) CloudFront with origin access control. (c) 1-hour presigned S3 GET URLs returned by the tool Lambdas.

**Decision.** (c). Private bucket with Block Public Access; only signed-in sessions receive working URLs.

**Consequences.** No CloudFront distribution. URLs expire, so the shelf refreshes URLs on each update. Signing is local and adds negligible latency.

**Amendment (2026-10-19).** Still holds with the synthetic catalog (ADR-27). The images are now generated, so licensing is no longer the reason. The private bucket keeps the sample secure by default and serves images only to signed-in shoppers. Each product has one 384 px WebP at `products/<asin>/image.webp`.

**Evidence.** research.md section [8](research.md#8-catalog-dataset-amazon-reviews-2023).

---

## ADR-14: Decider container architecture

**Status:** Accepted. Revised during the decider spike (weights baking removed; see Consequences). The S3-pulled venv and the EBS volume are superseded by ADR-22.

**Context.** The project convention builds agent images for ARM64 in CodeBuild. The GPU families we use (g5, g6, g6e) are x86_64.

**Decision.** The decider image is x86_64, built by CodeBuild on an x86_64 image. The session host image stays ARM64.

**Consequences.** Two CodeBuild projects with different environments.

**Revision (decider spike, 2026-10-16).** The original plan baked the ~5 GB model checkpoint into the image on a single-stage `python:3.12-slim` + CUDA-wheels build. The built image (CUDA devel toolchain plus the checkpoint) measured about 10.1 GB and failed to deploy: AgentCore Runtime enforces a hard, non-adjustable 2 GB Docker image size cap, confirmed in AWS's own quotas documentation (`bedrock-agentcore-limits.html`, "Maximum size for a Docker image in an AgentCore Runtime" = 2 GB, Adjustable: No) and reproduced directly against the control plane (`CreateAgentRuntime` returned `ServiceLimitExceeded: maxImageSizeMb limit exceeded`). Two changes bring the image under the cap:
1. **Multi-stage build.** A `devel`-base CUDA image (nvcc, headers) is used only to compile `causal-conv1d`'s kernel and build the Python venv; the final image copies just that venv onto a slim `runtime`-base CUDA image, which still has the CUDA runtime libraries the compiled extension needs but not the compiler toolchain.
2. **Weights no longer baked in.** The checkpoint is uploaded once to an S3 bucket and downloaded by the container's supervisor script at startup into the capacity provider's persistent EBS volume, not into the image. This runs once per instance lifetime (the volume persists across session continuations on the same instance, per ADR-16), so it is not on the per-sentence latency path; `HF_HUB_OFFLINE` is set only once the checkpoint is confirmed local, so steady-state inference still makes no outbound calls.

Builds still run outside the CDK deploy path with a 60-minute timeout, since the multi-stage compile step is not fast.

**Amendment (2026-10-19).** The capacity provider's persistent EBS volume no longer exists. On SageMaker (ADR-22) the CUDA/PyTorch environment is baked into the image again (no 2 GB cap), and the model files are mounted read-only from the model bucket at `/opt/ml/model`. The image is still x86_64 and built by CodeBuild.

**Amendment (2026-10-20): base image from Amazon ECR.** Container base images must come from ECR, not external registries, so the image no longer builds from `nvcr.io/nvidia/cuda` (12.4.1 devel + runtime). It is one stage on the AWS Deep Learning Containers base image on Amazon ECR Public (`deep-learning-containers/base`, CUDA 12.9 toolkit, Python 3.12, Ubuntu 22.04), which supplies nvcc for `causal-conv1d`. Torch still uses its cu126 wheels, which bundle the CUDA runtime.

**Evidence.** research.md section [5](research.md#5-agentcore-runtime-instances-gpu) (drivers provided, standard CUDA image works); the [Measurements](research.md#measurements) section records the 2 GB cap and the 10.1 GB measured image size that triggered this revision.

---

## ADR-15: Who decides and who speaks

**Status:** Accepted

**Context.** Sonic can call tools and decide on its own. If Sonic chose when to search or add to cart, the UI would wait on Sonic's tool calls and confidence would come from an uncalibrated source.

**Options considered.** (a) Give Sonic all Gateway tools (template pattern). (b) Start the decision pipeline in code on Sonic's final transcript; give Sonic one local `get_turn_outcome` tool plus read-only `get_product_detail`, and have it speak only what the outcome supports.

**Decision.** (b).

**Consequences.** The UI updates without waiting for Sonic. Spoken confidences match displayed ones. State-changing actions (search, add to cart) happen only in code. Risk: Sonic might reply without calling the tool. The UI stays correct regardless, and the system prompt and scripted tests check this.

**Evidence.** The per-sentence flow in `turn_pipeline.py`; research.md section [2](research.md#2-jev-and-system-one-decision-models) (System One / System Two split).

---

## ADR-16: Decider warm pool

**Superseded by [ADR-22](#adr-22-decider-on-a-sagemaker-real-time-endpoint) (2026-10-18):** an endpoint with a warm minimum replaces the fixed pool of session ids.

**Status:** Accepted. Lifecycle details TO VERIFY.

**Context.** On Instances, each `runtimeSessionId` maps to an EC2 instance, and the first call provisions it, which is slow. Sessions last up to 14 days.

**Options considered.** (a) One decider session per shopper (each shopper pays provisioning). (b) A fixed pool of stable session ids shared by all shoppers, pre-warmed at deploy.

**Decision.** (b). Default pool size 1 (configurable 1-4); the host maps a shopper to a pool id by hash. `deploy-all.sh` pre-warms, and `status.sh --warm` re-warms before each recording.

**Consequences.** Shoppers never see provisioning time unless the instance went cold, in which case the host degrades that turn and starts a background warm call. Whether `StopRuntimeSession` stops instance charges, and the idle timeout value, are TO VERIFY in the spike. If stopping does not release the instance, the documented way to stop GPU charges is destroying the decider stacks.

**Evidence.** research.md section [5](research.md#5-agentcore-runtime-instances-gpu).

## ADR-17: Cart and Orders as separate tables

**Status:** Accepted

**Context.** The original design modeled the cart with `add_to_cart`/`get_cart` tools and no distinct "order" concept — a shopper's cart was the only persistent record, and the "cart summary" was the closest thing to a checkout. The design called for a clearer, more realistic shopping flow: a per-session cart that accumulates multiple items across turns, and a distinct action to place the order.

**Options considered.** (a) Keep one cart record per session with no separate order step. (b) Add a `place_order` tool that reads the cart's current contents, writes a single order record to a new table, and clears the cart, while `add_to_cart` continues to write individual line items to a separate cart table.

**Decision.** (b). Two new DynamoDB tables: `VoiceShop-Cart` (PK = sessionId, SK = asin — one line item per row, so `add_to_cart` is a simple upsert and the full cart is one `Query` by PK) and `VoiceShop-Orders` (PK = sessionId, SK = orderId — one row per placed order, holding its line items, item count, total cents, and placed-at timestamp). A new `place_order` Gateway/Lambda tool reads the cart, writes the order, and deletes the cart's line items so both the table and the frontend `CartPanel` go empty. No payment step is added — "placing an order" in this demo means writing the record and clearing the cart, nothing more.

**Consequences.** `get_cart` is no longer a separate Gateway tool: the session host already has the cart's state from `add_to_cart`'s response and the cart table, so a frontend-facing cart read goes through the Lambda layer directly rather than a dedicated MCP tool. The `intent` typed question gains a `place_order` option alongside `confirm`, `narrowing`, `pointing`, `go_back`, and `start_over`, gated at the same act threshold (≥ 0.8) as other destructive/committing intents, per ADR-7. This does not change the per-sentence latency budget: placing an order is a terminal action taken once per shopping session, not a per-sentence step.

**Amendment (2026-10-19): transactional writes.** `add_to_cart` writes a per-request idempotency record and updates the cart line in one DynamoDB transaction, so a retried request changes nothing, whatever product it names. A line holds at most 10 of a product. `place_order` writes the order and deletes the cart lines in one transaction. Each delete is conditioned on the quantity it read, and the transaction is retried on a fresh read if the cart changed. Cart and order reads are strongly consistent, so a placed order is never reported as failed.

**Amendment (2026-10-20).** The act-threshold gate for `place_order` above no longer applies: an order is always offered first ("Should I place the order?"), at any confidence, and an empty cart is reported (ADR-31). A `remove_from_cart` tool deletes one cart line (ADR-34).

**Evidence.** Tool schemas in `backend/agentcore-gateway/cdk/lib/tool-schemas.ts`; `backend/backend-infrastructure/lambda/add-to-cart` and `place-order`.

---

## ADR-18: Gender as a typed slot

**Status:** Accepted

**Context.** "A gift for my mom" and "a gift for my parent" both need to resolve to a gendered product set for a useful hard filter, but "parent" alone does not say which gender. Without an explicit gender/demographic field and slot, the system has no principled way to filter or ask about this — and no vision model exists anywhere in this design (session host, Sonic, or decider) that could infer it from a product photo, so it must come from catalog metadata and be resolved the same way every other ambiguous slot is resolved.

**Options considered.** (a) Treat gender purely as a soft signal folded into the retrieval query text, with no hard filter and no dedicated question. (b) Add `gender`/demographic as its own typed-question slot (or a mapping derived from the `recipient` answer), using the same act (≥ 0.8, hard filter) / ask (< 0.5, clarifying question) / narrow (in between) bands as every other slot.

**Decision.** (b). When the decider's confidence in the shopper's intended gender is low (for example "parent" alone, which is genuinely ambiguous), Sonic asks a short clarifying question naming the leading options ("mom or dad?"), exactly as it would for any other low-confidence slot — no special-case logic. Once gender resolves at or above the act threshold, it commits as a hard filter in code, the same tier as budget and category (ADR-6).

**Consequences.** The catalog must carry a non-empty gender/demographic attribute per item for this filter to have anything to act on; an item missing it is excluded from the catalog rather than left unfiltered. The `recipient` slot's options (`self`, `parent`, `partner`, `child`, `friend`, `coworker`, `other`, `not_mentioned`) stay as they are — gender is a related but separate slot, not a replacement for recipient, because "parent" is useful context (for the "why this pick" explanation and Memory) even once gender is resolved.

**Amendment (2026-10-18).** Recipients became fine-grained (`mother`, `father`, `parent`, `wife`, `husband`, `daughter`, `son` and so on), so a gendered recipient implies the gender and "parent" alone leads to "Is that your mom or your dad?". With the synthetic catalog (ADR-27) the generator writes each product's gender, so every item has one. See [How we tuned it](how-we-tuned-it.md).

**Evidence.** `backend/agentcore-runtime/agent/questions.py`; ADR-7 (thresholds).

---

## ADR-19: Pre-curated catalog asset

**Status:** Superseded by [ADR-27](#adr-27-synthetic-catalog) (2026-10-19). The release asset, the release-asset bucket, `precurate.py`, `download.py`, the Phase A/B split and the offline wheelhouse are removed. Kept for the record.

**Context.** The original design downloaded `meta_Amazon_Fashion.jsonl` live from the Amazon Reviews 2023 distribution during the curation CodeBuild step, on every deploy. The design goal was to avoid a live download on each deploy, for reproducibility (every deployment should curate from the same known-good data, not whatever the live distribution happens to serve that day) and to decouple dataset availability from deploy reliability.

**Options considered.** (a) Keep the live download inside the VPC-attached curation CodeBuild step, as originally designed. (b) Curate the eligible item set once, up front, against the live dataset; bundle the selected items' metadata and already-downloaded images as a zipped, versioned release asset (GitHub Release or S3 object); have deploy-time curation fetch that asset instead of the live dataset.

**Decision.** (b). Curation becomes two CodeBuild phases: Phase A (`VoiceShop-CatalogDownload`, not VPC-attached) fetches the pre-curated release asset; Phase B (`VoiceShop-CatalogCuration`, in the VPC) reads the fetched data and writes to OpenSearch Serverless, DynamoDB, and S3. This split existed because of ADR-2/ADR-8's no-NAT-gateway private subnets: Phase A needs outbound internet (to fetch the asset) and Phase B needs the AOSS VPC endpoint, and the no-NAT subnets provide only the latter.

**Consequences.** The curation job was no longer tied to the live availability or format of the dataset distribution at deploy time; a deploy was reproducible against a fixed, known-good asset. The one-time pre-curation step still ran the filter-and-select logic once against the live dataset, so the eligibility rules and the usage-terms obligations were unchanged. `CATALOG_SIZE` was raised from 500-1,500 (default 1,000) to 1,000-10,000 (default 7,000) at the same time, for a different reason: more per-category depth and a more realistic load on retrieval.

ADR-27 keeps the idea that worked (a pinned, versioned catalog file checked by sha256) and drops the rest: the catalog is generated, ships in the repository, and one job loads it. The job runs outside the VPC and hands the index documents to the in-VPC index writer (ADR-32).

**Evidence.** [research.md section 8](research.md#8-catalog-dataset-amazon-reviews-2023).

---

## ADR-20: Catalog eligibility from the item's own text

**Status.** Superseded by [ADR-27](#adr-27-synthetic-catalog) (2026-10-19). The synthetic catalog writes every attribute directly, so there are no eligibility rules or mapping tables. The index rebuild on each run (second paragraph of the decision) still holds. Kept for the record.

**Context.** The catalog build (2026-10-18) ran the original eligibility filter against the full `meta_Amazon_Fashion.jsonl.gz` (826,108 records). Requiring structured `details.Color`, `details.Material`, `details.Size` and `details.Department` plus a mapped category left **147** items, below the 1,000-item minimum. Price is the main limit: only 50,249 records have one, and 17,491 have price, title, description, MAIN image and brand together. Within those, `details.Color` is set on 2,243, `details.Material` on 2,201 and `details.Size` on 720. The dataset's `categories` field is empty for Amazon_Fashion, so category can only come from the title.

**Options considered.** (a) Keep the strict structured-only rule (147 items, violates the 1,000 minimum). (b) Text-derived color, material and gender, size still required (~1,278 items, mostly jewelry). (c) Text-derived color, material and gender, size optional (~5,200 items). (d) Switch to the much larger Clothing_Shoes_and_Jewelry category (changes ADR-5/R10.1).

**Decision.** (c), a design decision. Color and material come first from equivalent `details` fields (`attribute_map.json`), but only if the value contains a known vocabulary term, since raw values include junk like Color "aunt" and Material "base". Otherwise they come from the first vocabulary term in the title, then in the features. Gender comes from `Department`/`Target Audience`, falling back to title keywords (`gender_map.json`). Category comes from title keywords (`category_map.json`): watches and sunglasses win outright, and otherwise the earliest keyword in the title wins. Size is kept when present and otherwise left empty. Every value comes from the item's own metadata, with no vision model and no backfill from other items. Items still missing a required attribute are excluded and counted.

Separately, `VoiceShop-SearchStack`'s collection is a Classic vector collection, which does not accept caller-supplied document ids (the AWS docs list custom document IDs only for NextGen collections). So `_id = parent_asin` is not possible. The curation job deletes and recreates the `products` index on every run instead. DynamoDB keys stay deterministic, and products dropped from the selection are deleted. Reruns therefore create no duplicates in either store.

**Consequences.** The v1 release asset has 5,236 items (5,237 eligible, 1 image download failed), and `catalogSize` defaults to 5,000. Jewelry is the largest category (~46% of eligible items); selection gives each category an even share and redistributes what small categories cannot fill. Text-derived attributes are noisier than structured ones (for example, "gold" is both a color and a material for plated jewelry), which is acceptable for a demo catalog. 55 items cost under $2. The search index is briefly empty during a curation run.

**Evidence.** [research.md section 8](research.md#8-catalog-dataset-amazon-reviews-2023); the manifest counts of the removed `precurate.py`; AWS docs "Working with vector search collections" (NextGen: custom doc ID support).

---

## ADR-21: `no_match` from an explicit "none of these" option

**Status.** Accepted (2026-10-18). What the shopper sees on a no-match is amended by [ADR-29](#adr-29-no-match-keeps-the-closest-items-and-offers-to-widen) (2026-10-19).

**Context.** The design gave `best_fit` the same act / ask / narrow bands as the slot questions. It declared `no_match` when the top probability was below `ASK_THRESHOLD` (0.5) and less than twice the second-highest. A study (2026-10-18) ran `best_fit` over 50 real `search_catalog` candidates for 22 shopper requests. The picks were sensible, but the distributions were flat: top probability 0.06–0.27 and `confidence` ≤ 0.25, so `best_fit` never gets near the 0.8 act threshold. The ratio rule fired on 12 of 16 requests the catalog can satisfy, with ratio medians of 1.54 (in catalog) vs 1.42 (not in catalog).

**Options considered.** (a) Add a `none_of_these` criterion to `best_fit` and declare `no_match` above a threshold. (b) Add a separate `noul` "is any candidate a good match?" question. (c) Keep the ratio rule with fewer options per round (needs another GPU test).

**Decision.** (a), a design decision. `best_fit` sends at most 49 candidates plus `none_of_these`. If `none_of_these` ≥ `NO_MATCH_THRESHOLD` (default 0.135), the outcome is `no_match`. Otherwise the shelf is the candidates in `best_fit` order. No per-pick confidence is shown in the UI or spoken by Sonic, and there is no "Best pick" badge. The act / ask / narrow bands stay for the slot questions only, and "tentative" now means "narrowed by a slot value below the act threshold".

**Consequences.** The `no_match` rule, `ui.shelf` (no `confidence`), `ShelfGrid` (no confidence bar) and "why this pick" (rank instead of probability) change accordingly. The threshold margin is thin: in-catalog requests were ≤ 0.127 and truly absent ones ≥ 0.142. Option (b) separated less cleanly (it missed the "tuxedo with tails" case). `NO_MATCH_THRESHOLD` is therefore a session host environment value (default 0.135), to be re-derived from a labeled no-match set. The measurement was made on the earlier fashion catalog.

**Amendment (2026-10-19).** Re-derived on the synthetic catalog's labeled set (`eval/catalog`): at 0.135, 14 of 61 fitting requests were reported as no-match. The threshold is now 0.35, in `calibration.json` (`noMatch`): 2 of 61 fitting requests flagged and 11 of 13 out-of-catalog ones caught on the tuning split; 0 of 26 and 5 of 6 held-out. `NO_MATCH_THRESHOLD` still overrides it.

**Amendment (2026-10-19).** A no-match no longer hides the shelf or keeps the previous one. The closest items stay, marked tentative, and the turn ends with one spoken yes/no offer to widen the search. See [ADR-29](#adr-29-no-match-keeps-the-closest-items-and-offers-to-widen).

**Evidence.** docs/research.md "`best_fit` distributions and the `no_match` rule" (the `best-fit-distributions.py` script was written for the earlier catalog and has been removed); for the 2026-10-19 amendment, `backend/agentcore-runtime/eval/catalog`.

---

## ADR-22: Decider on a SageMaker real-time endpoint

**Status.** Accepted (design decision, 2026-10-18). Supersedes ADR-2 and ADR-16. Amends ADR-14.

**Context.** The goal was to design the decider for production scale (thousands of shoppers). On AgentCore Runtime Instances, each runtime session id is one EC2 GPU instance. There is no load balancer and no scaling on load. The decider spike measured:

- InvokeAgentRuntime overhead of 131 / 200 ms p50 / p95;
- a 194 s cold start for a new session and 127.5 s after an idle stop;
- idle instances stopped after ~15 minutes, with persistent volumes left behind.

Scaling would mean building a pool manager: routing, scheduled scaling, keep-alive calls, quotas.

**Options considered.**

| Option | Price per g6.xlarge hour (us-east-1, on-demand, 2026-10-18) | Scaling |
|---|---|---|
| (a) AgentCore Instances plus a self-built pool manager | ~$0.868 (EC2 $0.8048 + 7.8% G-series management fee) | Build it |
| (b) SageMaker real-time endpoint | $1.1267 (ml.g6.xlarge hosting) | Load balancing across instances, target-tracking auto-scaling, health checks and replacement, Multi-AZ placement, model copies per GPU via inference components |
| (c) ECS/EKS on EC2 GPUs | $0.8048 | Self-managed |

**Decision.** (b). The decider runs on the endpoint `voiceshop-decider`:

- **Capacity:** ml.g6.xlarge, at least one instance always warm (`deciderMinInstances`, default 1), auto-scaling on `SageMakerVariantInvocationsPerInstance` with a target of 120 per minute, up to `deciderMaxInstances`.
- **Network:** in the existing private subnets, with network isolation. (Amended 2026-10-19: no VPC; network isolation without a `VpcConfig`, see ADR-26.)
- **Model files:** the checkpoint and the base-model Hugging Face cache live in a model bucket under `model/`, mounted read-only at `/opt/ml/model`.
- **Image:** the CUDA/PyTorch environment is baked in. SageMaker has no 2 GB image cap, so ADR-14's S3-pulled venv is gone.
- **Health check:** `/ping` returns 200 only once the model is loaded. That's SageMaker's contract, the opposite of the AgentCore workaround.

The AgentCore decider stacks, pool logic, measurement project and volume cleanup are removed. The session host keeps using AgentCore Runtime (microVM per shopping session), and only `decider_client.py` changes (`sagemaker-runtime` InvokeEndpoint instead of InvokeAgentRuntime).

**Consequences.**

- **Cost:** about 30% more per GPU-hour than AgentCore Instances. The warm minimum bills around the clock while deployed (~$822/month for one ml.g6.xlarge; about $1,028/month at $1.408/hour for the ml.g5.xlarge default since the 2026-10-19 amendment); `cleanup-all.sh` deletes it.
- **Cold starts:** no shopper ever waits for one; scale-out adds capacity in minutes.
- **Quota:** a new account's endpoint quota for the instance type may be 1, so `deciderMaxInstances` defaults to 1 until it is raised.
- **Network:** the ECR/STS interface endpoints added for the capacity provider were not used by the endpoint and were removed. The whole VPC was removed later (ADR-26).
- **Re-measure:** InvokeEndpoint overhead was re-measured (8.8 ms p50 in-Region). Server-side decider times, the `best_fit` distribution results and ADR-21 are unaffected, because the container and model are the same.
- **Throughput:** batching requests and several model copies per GPU remain the largest throughput and cost levers (production considerations).

**Amendment (2026-10-19): default instance ml.g5.xlarge.** The first fresh deploy failed with InsufficientInstanceCapacity for ml.g6.xlarge in us-east-1. The default is now ml.g5.xlarge (NVIDIA A10G, 24 GB, $1.408/hour on-demand in us-east-1 against $1.1267 for ml.g6.xlarge), and `deploy-all.sh --decider-instance-type` picks another type; the quota check follows the chosen type. The latency numbers above were measured on ml.g6.xlarge.

**Evidence.** AWS Price List API (EC2, AmazonSageMaker), 2026-10-18; AgentCore pricing page; docs/research.md "Instances lifecycle"; Service Quotas `L-D470D954` (ml.g6.xlarge) and `L-1928E07B` (ml.g5.xlarge, the default).

---

## ADR-23: Visual-first, spoken replies optional

**Status.** Accepted (design decision, 2026-10-18). Amends ADR-15.

**Context.** A design review of the frontend found that shopping here is a visual experience. Every sentence visibly changes the chips and the shelf, so a spoken reply after every sentence distracts and feels like lag, even though the UI does not wait for it.

**Options considered.**

- (a) Nova Sonic always handles speech-to-text; spoken replies are off by default (audio not played, Sonic told to keep replies minimal) and a toggle turns them on.
- (b) Amazon Transcribe streaming for speech-to-text by default, opening a Sonic stream only when spoken replies are on.

**Decision.** (a). One speech path, and Nova Sonic stays central to the sample. The browser sends `ui.voice_replies {enabled}`, remembers the choice per browser, and does not play Sonic audio while it is off. Clarifying questions, `no_match` and offers such as "add it to your cart?" are always shown on screen, and also spoken when replies are on.

**Consequences.** In visual mode Sonic still produces speech that is not played; that cost is accepted for a single speech path (option (b) remains the cheaper alternative at scale). The session host must honour `ui.voice_replies` and keep the on-screen prompts complete, because they are the primary channel.

**Amendment (2026-10-19).** Yes/no confirmations are spoken even when replies are off: "Do you mean No. N, ...?" (ADR-28), the widen offer after a no-match (ADR-29), the order offer (always asked first, ADR-31), and the offer an unsure `go_back`, `start_over` or `confirm` turns into. The host decides per turn whether audio reaches the browser. The header toggle reads "Voice on" or "Voice off".

**Evidence.** Design review of the frontend, 2026-10-18.

---

## ADR-24: Speculative retrieval on partial transcripts

**Status.** Accepted (design decision, 2026-10-18). Amends the per-sentence flow and the latency budget.

**Context.** A Sonic measurement (2026-10-18) found ~850 ms from the end of speech to Nova 2.5 Sonic's final user transcript (budgeted at 350 ms), and `endpointingSensitivity` did not change it. Starting everything on the final transcript would give a first UI update of ~1.14 s (goal 1.0 s) and a shelf refresh of ~1.41 s (goal 1.3 s). Retrieval (~262 ms) is the slower of the two parallel steps.

**Options considered.** (a) Start retrieval on partial transcripts and re-budget. (b) Only re-budget (~1.2 s / ~1.5 s). (c) Measure Amazon Transcribe streaming for speech-to-text first (ADR-23 option B).

**Decision.** (a). The host starts `search_catalog` once the partial user transcript has been stable for 250 ms (or on the first delta after speech ends). On the final transcript it reuses that result only if the normalized query is identical; otherwise it cancels the speculative search and searches again. Partial transcripts never commit chips, call the decider or touch the shelf. Goals become: first UI ≤ 1.1 s, shelf ≤ 1.3 s, pointing ≤ 1.2 s, all from the end of speech.

**Consequences.** Some extra `search_catalog` calls when the final transcript differs from the partial (the cost of a wasted Lambda + Titan + kNN call). The turn pipeline needs cancellation for superseded speculative searches. A speculative miss costs ~260 ms on that turn. `ui.debug` records whether retrieval was speculative, so the hit rate can be measured.

**Evidence.** docs/research.md "Nova 2.5 Sonic"; `backend/agentcore-runtime/scripts/sonic-check.py`.

## ADR-25: Catalog updates checked with the decider before publishing

**Status.** Proposed, documented only; not implemented in this sample (design decision, 2026-10-18).

**Context.** The sample ships a generated catalog that is loaded as a whole on each deploy (ADR-27). In production, new products arrive continuously as images. Two things can break when the catalog changes, even if every item's metadata is correct:
- **New values:** new departments, categories or gender values change the decider's option lists (ADR-30).
- **Lookalikes:** new items with near-identical titles make "the second one" harder to resolve. Measured on a real shelf of watches, near-identical titles were the main cause of pointing errors.

**Options considered.**
- (a) Publish new items straight after metadata extraction.
- (b) Extract metadata, then gate publishing on decider checks against a staged catalog.
- (c) Like (b), but retune the thresholds automatically when checks fail.

**Decision.** (b). The flow:
- S3 upload, EventBridge, then one Step Functions run per batch.
- A multimodal Amazon Nova model fills the store's fixed metadata schema, including a distinctive title.
- Code validates the schema.
- Items are staged in DynamoDB and a staging search index.
- Three decider checks run: understand questions, findability, pointing.
- Items are published only if they pass; otherwise they are flagged for review.

Option (c) is rejected: lowering thresholds until checks pass tunes the result instead of measuring it. Recalibration stays a reviewed change. Details in `docs/catalog-updates-in-production.md`.

**Consequences.**
- **Speed:** new items wait for a batch check, for example up to 15 minutes, before shoppers see them.
- **Load:** the checks add decider traffic; run them off-peak or on a separate endpoint (ADR-22).
- **Data:** the near-miss sentences become the labeled no-match set that ADR-21's threshold needs.
- **Anchor:** a human-written held-out set remains the reference, because sentences generated from an item's own metadata partly test it against itself.

---

## ADR-26: No VPC

**Status.** Accepted (design decision, 2026-10-19). Supersedes the networking amendments of ADR-2 and ADR-8. Amends ADR-22. **The search part (public collection endpoint, every Lambda outside a VPC) is superseded by [ADR-32](#adr-32-private-search-collection)**; the decider part (network isolation, no VPC) stands.

**Context.** VoiceShop had a VPC (`VoiceShop-NetworkStack`: private subnets, no NAT gateway, S3 and DynamoDB gateway endpoints, OpenSearch Serverless and CloudWatch Logs interface endpoints). It existed for one reason: the AgentCore Runtime Instances capacity provider requires a VPC (ADR-2). ADR-22 replaced that hosting with a SageMaker endpoint, which does not need one. What was left in the VPC had a cost and no remaining purpose:

- Interface endpoints bill per hour in every Availability Zone, whether or not anything uses them.
- Lambda functions and SageMaker endpoints attached to a VPC hold elastic network interfaces. Deleting their stacks waits for those interfaces to be released, which took 20 to 40 minutes per teardown.
- The catalog load needed two CodeBuild phases: one outside the VPC to fetch data, one inside it to reach the search collection (ADR-19).

**Options considered.**

| Option | For | Against |
|---|---|---|
| (a) Keep the VPC | Network-layer boundary around the collection and the Lambdas | Cost and slow teardown for no platform requirement; two-phase catalog load |
| (b) No VPC; access control by IAM | Simpler, cheaper, fast teardown; one catalog job | The collection has a public endpoint (requests still need SigV4 and a data access policy) |

**Decision.** (b).

- `VoiceShop-NetworkStack` is deleted.
- The OpenSearch Serverless network policy allows the public endpoint (superseded by ADR-32: VPC endpoint only). Every request must still be SigV4-signed by a role that a data access policy names: the `search-catalog` Lambda role (read) and the curation job role (write; since ADR-32 the `catalog-index-writer` role). Anonymous requests are rejected.
- The four tool Lambdas run outside any VPC and call AWS service endpoints over TLS. (Superseded by ADR-32: `search-catalog` and `catalog-index-writer` run in a VPC; the other tool Lambdas, now including `remove-from-cart`, stay outside.)
- The decider endpoint keeps network isolation (`EnableNetworkIsolation`) with no `VpcConfig`, security group or EC2 permissions. The container has no network access at all; SageMaker mounts the model files before it starts.
- The catalog curation CodeBuild job runs without a VPC (ADR-27).

**Consequences.**

- No VPC endpoint charges and no interface-release waits on teardown. (No longer true since ADR-32: two interface endpoints bill per hour, and teardown waits for the VPC functions' network interfaces.)
- CDK tests asserted no VPC configuration, security groups or VPC endpoints. (Since ADR-32 they assert the private search network instead.)
- `cleanup-all.sh` still deletes `VoiceShop-NetworkStack` if an older deployment left one.
- A deployment that requires private network paths can add VPC endpoints back. (ADR-32 did this for search.)

**Evidence.** `backend/backend-infrastructure/lib/search-stack.ts` (network policy), `backend/decider-runtime/cdk/lib/decider-endpoint-stack.ts` (network isolation), the CDK tests in each app.

---

## ADR-27: Synthetic catalog

**Status.** Accepted (design decision, 2026-10-19). Supersedes ADR-5, ADR-19 and ADR-20. ADR-13 still holds.

**Context.** VoiceShop used items from the Amazon Reviews 2023 dataset (ADR-5). Before a public release we checked the terms again:

- The dataset declares no license.
- Its product images are Amazon-hosted photos owned by brands and sellers.
- Nothing grants the right to redistribute the metadata or the images.

A public sample cannot ship that data, and it cannot ask every deployer to download it either. The dataset also caused design work of its own: most items lacked structured attributes, so ADR-20 read them from titles, and the catalog was fashion only.

**Options considered.**

| Option | For | Against |
|---|---|---|
| (a) Keep the dataset, terms TO VERIFY | Real products | No license; images owned by third parties; cannot be published |
| (b) Another public dataset (Amazon Berkeley Objects, Kaggle scrapes) | Real images | No prices, or unclear licenses (see ADR-5) |
| (c) Generate the catalog with models on Amazon Bedrock | Owned by the project; every attribute present; any taxonomy; ships with the repository | Generated content can have artifacts; the generation is a one-time cost and effort |

**Decision.** (c). VoiceShop ships its own generated catalog.

- **Taxonomy.** `backend/catalog-data/generate/taxonomy.json`: 10 departments with 5 categories each (50 categories). Each category lists example products, materials, genders and a price range.
- **Generator.** `backend/catalog-data/generate/generate_catalog.py`, run once by the maintainer, never by a deploy. Stages:
  1. **specs:** Claude Sonnet 5 on Amazon Bedrock writes the products, about 50 per category: invented brands, a distinctive title of at most 60 characters, gender, color, material, size, price, rating, features, description and a photo prompt. It writes about 10% extra so enough pass image QA.
  2. **images:** Stability AI Stable Image Core (in us-west-2) draws one studio photo per product, with a seed per product.
  3. **qa:** Claude Haiku 4.5 checks each image: it shows the product in its color, with no text, logo or person. Rejected images are redrawn with a new seed, up to 2 times.
  4. **whiten:** Stability AI Remove Background cuts the product out, and it is placed on pure white.
  5. **package:** each image is downscaled to a 384 px WebP (about 10 KB). The products, images and a manifest (counts, models, taxonomy) go into one zip.

  Categories that end up short of products are topped up with more products, drawn and checked the same way (up to 3 rounds).
- **In the repository.** `backend/catalog-data/catalog/voiceshop-catalog-v2.zip`, pinned by `catalog/catalog.lock.json` (file name and sha256). About 2,500 products.
- **Loader.** One CodeBuild job, `VoiceShop-CatalogCuration`, with no VPC and no source bucket: the `catalog-data` package itself is the job's source, uploaded as a CDK asset. `curate.py`:
  1. checks the zip against the lock file;
  2. validates each row and uploads its image to `products/<asin>/image.webp` in the private images bucket;
  3. embeds each product with Titan Text Embeddings V2;
  4. rebuilds the `products` search index (ADR-20's rebuild-on-each-run rule still holds);
  5. writes `VoiceShop-Products` and `CATALOG#META`: departments with their categories, and option descriptions from the taxonomy;
  6. removes products and images that are no longer in the catalog.
- **Availability.** About 1 product in 20 (chosen by id) is marked unavailable, to exercise the availability filter.
- **Removed.** `precurate.py`, `download.py`, the Phase A/B split, the offline wheelhouse, the release-asset bucket, the eligibility rules and the dataset mapping tables.

**Consequences.**

- The sample has no third-party product data or images. The UI and README carry no dataset citation.
- Deploys are reproducible: the same zip, checked by hash, every time. Changing the catalog is a reviewed commit of a new zip and lock file.
- Department and category options come from `CATALOG#META`, so the session host has no catalog-specific constants (ADR-30).
- Regenerating the catalog needs Bedrock model access to Claude Sonnet 5, Claude Haiku 4.5 and the Stability AI models. Deploying does not.
- Generated images can still have small artifacts that QA missed.
- The labeled sets and thresholds in [How we tuned it](how-we-tuned-it.md) were first measured on the earlier fashion catalog. (Amendment 2026-10-20: a labeled set on the synthetic catalog now exists, `eval/catalog`: 116 scenarios, 221 turns, tuning and held-out splits. The department, category, offer_reply and noMatch thresholds were recalibrated on it on 2026-10-19, and the others were re-checked.)

**Evidence.** `backend/catalog-data/generate/`, `backend/catalog-data/curate.py`, `backend/catalog-data/cdk/lib/catalog-data-stack.ts`; [research.md section 8](research.md#8-catalog-dataset-amazon-reviews-2023).

---

## ADR-28: Pointing by on-screen number

**Status.** Accepted (design decision, 2026-10-19).

**Context.** "The second one" or "the dog watch" must open the right card. On a real shelf of 12 women's watches, 8 with near-identical titles, the `pointed_item` question sometimes chose the wrong card. We tried adding three position questions in the same call (which number, which row and which side the shopper said), plus rules to combine them with the item question. On the sentences we tuned with, it looked better. On a held-out set of 40 sentences written afterwards, it opened the right card 12 times out of 40, against 27 for the item question alone. The decider does not answer "which number did the shopper say" literally: for "the dog watch" it answers that watch's number, so the extra questions repeated the item question instead of adding a signal.

**Options considered.**

- (a) Item question plus position questions and resolver rules.
- (b) Item question alone, with numbered cards and a confirmation by number when unsure.

**Decision.** (b). The position questions and resolver rules are removed.

- Every card shows its on-screen number. Tapping a card always works.
- The `pointed_item` options describe each card by number, ordinal, row and side, then title and color. The shelf listing goes only to this question.
- Thresholds (`calibration.json`):
  - confidence ≥ 0.20: open the card. The reply names it: "Opened No. N, <short title>, <price>. Should I add it to your cart?"
  - 0.10 to 0.20: ask "Do you mean No. N, <short title>?". This yes/no question is spoken even when spoken replies are off. "Yes" opens it.
  - below 0.10: ask "Which number do you mean? You can also tap it."

**Consequences.** Held-out results (40 sentences, women's watches shelf, 3 runs, identical each run):

| Outcome | Count |
|---|---|
| Opened the right card | 27 |
| Opened a wrong card | 5 |
| "Do you mean No. N?" with the right number | 2 |
| "Do you mean No. N?" with a wrong number | 2 |
| "Which number?" | 4 |

84% of opens were right. The wrong opens are row-relative phrases such as "bottom right". Opening is reversible, so a wrong open costs one "no, number 12". This is the clearest case for fine-tuning. The shelf was from the earlier fashion catalog; re-run the evaluation on the synthetic catalog.

**Evidence.** The earlier catalog's `pointing_eval.py` (removed with that catalog, with its shelves and result files; the current labeled set is `backend/agentcore-runtime/eval/catalog`); [How we tuned it](how-we-tuned-it.md#pointing-confirm-by-the-on-screen-number).

---

## ADR-29: No-match keeps the closest items and offers to widen

**Status.** Accepted (design decision, 2026-10-19). Amends ADR-21.

**Context.** ADR-21 decides *when* a request has no match (`none_of_these` ≥ `NO_MATCH_THRESHOLD`). It did not settle what the shopper then sees. The first version cleared the shelf and asked "What should I change?". That left nothing to look at, and an open question is harder to answer by voice than a yes/no.

**Options considered.**

- (a) Clear the shelf and ask an open question.
- (b) Keep the previous shelf.
- (c) Show the closest items, marked tentative, and offer to drop one filter.

**Decision.** (c).

- A no-match never hides the shelf. The closest items (in `best_fit` order) stay, marked tentative, with the notice "No exact match. These are the closest."
- The turn ends with one yes/no offer, spoken even when spoken replies are off. To choose it, the host searches once without each committed hard filter (category, department, gender, budget), in parallel, and counts what passes the other filters. It offers to drop the filter that adds the most matches, for example "Should I look beyond watches? That shows N matches."
- "Yes" drops that filter and searches again. Like every offer, it answers only the next sentence.
- If no filter adds matches, the turn asks "What should I change?".
- The shelf is empty only when nothing at all passes the hard filters.

**Consequences.** A no-match turn makes one extra search per committed filter, in parallel. The shopper always sees products and a one-word way forward.

**Evidence.** `backend/agentcore-runtime/agent/turn_pipeline.py` (`_no_match_outcome`, `_widen_offer`).

---

## ADR-30: Department then category questions

**Status.** Accepted (design decision, 2026-10-19). Thresholds measured on `eval/catalog` on 2026-10-19 (amended below).

**Context.** The synthetic catalog has 50 categories (ADR-27). A single 50-way category question spreads the decider's probability over many options and gives lower, less calibrated confidences. The category threshold (act 0.85) was measured with 11 fashion categories.

**Options considered.**

- (a) One category question with all 50 options.
- (b) A department question (10 options), then a category question within the department (about 5 options).

**Decision.** (b).

- `department` is a new slot: a chip, a hard filter and a change-of-mind target like the others. Its 10 options are each described by their categories.
- The category question is asked only within a department. Its options are described by example products.
- When the department commits in a sentence, a category round in the same sentence asks which of its categories the shopper means.
- A change of mind is found by re-checking every chip with the latest sentence (ADR-33). A new department clears its category, and the category round asks within the new one. Removing the department clears its category.
- Options and descriptions come from `CATALOG#META`, written by the loader from the catalog's taxonomy. The host has no catalog-specific constants.
- Thresholds: department act 0.60, ask 0.35, **provisional** until a labeled set on the synthetic catalog exists. The category thresholds (0.85 / 0.40) are from the fashion catalog and must be recalibrated with it.

  **Amendment (2026-10-19).** Measured on the synthetic catalog's labeled set (`eval/catalog`, 116 scenarios, 221 turns, tuning / held-out): department act 0.40, ask 0.25 (precision 1.000 on both splits, recall 0.794 / 0.733; no wrong commit at any threshold); category act 0.55, ask 0.35 (precision 1.000, recall 0.864 / 0.857; at the old 0.85, recall 0.455).

**Consequences.** One more decider call on the sentence where the department commits. The same pattern fits any domain where the options grow: ask a coarse question first and a narrow one inside it. The fashion catalog's `eval/run_eval.py` and its results were removed with that catalog; their numbers stay in [How we tuned it](how-we-tuned-it.md) as the historical record, and `eval/catalog` is the current method.

**Evidence.** `backend/agentcore-runtime/agent/questions.py`, `policy.py`, `turn_pipeline.py` (`_category_round`), `agent/calibration.json`; [How we tuned it](how-we-tuned-it.md#applying-this-to-your-domain).

## ADR-31: Offer replies, screen actions and orders

**Status.** Accepted (design decision, 2026-10-19). The `offer_reply` thresholds were measured on `eval/catalog` on 2026-10-19: act 0.65, ask 0.35 (precision 1.000 on both splits, recall 0.656 / 0.688).

**Context.** The first live session on the synthetic catalog showed three gaps:

- **Replies to offers.** "Yeah" to "Should I go back to the previous results?" scored confirm 0.41 among the intents and was asked again. Worse, "no" scored go_back 0.75, above act, so saying no went back.
- **Screen actions.** The intent options had no way to say "close this" or "scroll down". "Can you close this" scored place_order 0.50 and became "Should I place the order?"; "scroll down" became a search with no match.
- **Orders.** A confident `place_order` placed the order with no question, and the misreading above came within 0.01 of that.

**Decision.**

- **The reply to an offer is its own question.** While an offer is pending, `offer_reply` (yes / no / new request) is asked in its own parallel call with only the assistant's question and the shopper's reply. A confident yes carries out the offer; a confident no drops it and changes nothing; anything else goes through the intent. With the shop state in the context, "yeah" scored yes 0.22 and "nope" 0.37; with only the question and the reply, every yes tried scored at least 0.84 and every no at least 0.73.
- **Screen actions are intents.** `close_item` (offered only while a product is open) and `move_screen` (only with a shelf). The pointing description covers corrections ("number five, not six"). On 24 sentences from the session, top-1 went from 17 to 20; "close this" became close_item 0.90, "scroll down" move_screen 0.97, "number five not six" pointing 0.93. A `move_screen` turn asks the direction (down / up / top) in a separate call and sends `ui.scroll`; the shelf does not change.
- **An order is always offered first**, at any confidence. "Yes" places it. An empty cart is reported instead of offered.

**Consequences.** One extra decider call per turn while an offer is pending, and one per scroll. Below act, `close_item` and `move_screen` narrow like any intent without an offer. Placing an order always takes two sentences.

**Evidence.** `backend/agentcore-runtime/agent/questions.py` (`offer_reply_question`, `INTENT_DESCRIPTIONS`, `scroll_question`), `policy.py`, `turn_pipeline.py`, `agent/calibration.json`.

## ADR-32: Private search collection

**Status.** Accepted (design decision, 2026-10-19). Supersedes the search part of ADR-26.

**Context.** Under ADR-26 the search collection allowed its public endpoint, with access controlled by IAM alone. Public network access to a search index is not acceptable for production, even behind SigV4, and organizations commonly enforce this with automated policy checks. A check like that can rewrite the network policy of a running collection, and every search then fails with HTTP 401. A production storefront does not expose its search index to the public network. OpenSearch Serverless offers three ways in: the public endpoint, a VPC endpoint, or access by an AWS service, and the only service it allows is Amazon Bedrock (Knowledge Bases). Our search Lambda and catalog loader are neither.

**Options considered.**

| Option | For | Against |
|---|---|---|
| (a) Amazon S3 Vectors instead of OpenSearch | IAM-only API with no network policy; low cost | No keyword or hybrid search, no counts or aggregations, no sorting; built for large, rarely queried vector sets rather than a storefront's hot index |
| (b) OpenSearch behind a VPC endpoint; the functions that call it in a VPC | What production customers run; keeps the search code and its measurements | A VPC, two interface endpoints, slower teardown |
| (c) A Bedrock Knowledge Base over the collection (service access) | No VPC | Ingestion through Bedrock, no counts, less control over the index; the vector index must be created by a direct call first |

**Decision.** (b).

- **Network** (`VoiceShop-NetworkStack`): one isolated subnet in one Availability Zone, no NAT gateway and no internet route; an S3 gateway endpoint, a Bedrock runtime interface endpoint (query embeddings) and flow logs.
- **Search** (`VoiceShop-SearchStack`): an OpenSearch Serverless VPC endpoint. The network policy sets `AllowFromPublic: false` and allows only that endpoint. Data access policies still name each role.
- **Functions:** only `search-catalog` (read) and the new `catalog-index-writer` (write) run in the VPC. `get-product-detail`, `add-to-cart`, `remove-from-cart` and `place-order` reach only DynamoDB and S3 and stay outside it.
- **Catalog load:** the CodeBuild loader stays outside the VPC: it installs its packages from the internet, which an isolated subnet would only allow through a NAT gateway. It writes S3 and DynamoDB and computes the embeddings as before, then writes the finished index documents to the images bucket (`catalog-index/`) and invokes `catalog-index-writer`, which rebuilds the index inside the VPC and returns the counts. The loader has no OpenSearch permissions at all.
- **One Availability Zone** to keep the sample cheap. The OpenSearch Serverless endpoint service supports only some zones (in us-east-1: 1a, 1b and 1d); the zone is `VOICESHOP_AZ`, default `<region>a`.

**Production.** Use at least two Availability Zones: a subnet in each for the functions, the Bedrock runtime endpoint and the OpenSearch Serverless VPC endpoint. One zone is a single point of failure for search.

**Consequences.**

- Two interface endpoints bill per hour while deployed (the S3 gateway endpoint is free).
- Deploy takes a few minutes longer (the network stack, and VPC functions are slower to create).
- Teardown waits while AWS releases the VPC functions' network interfaces before their security groups and the subnet can be deleted: about 24 minutes for `VoiceShop-LambdaStack` on 2026-10-20 (10 to 30 minutes seen). `cleanup-all.sh` waits for it and then checks that no VPC or OpenSearch VPC endpoint is left.
- S3 Vectors remains a fit for a very large catalog that is rarely queried, or as a cold tier behind OpenSearch.

**Evidence.** `backend/backend-infrastructure/lib/network-stack.ts`, `search-stack.ts`, `lambda-stack.ts`, `lambda/catalog-index-writer/index.js`; `backend/catalog-data/curate.py`, `cdk/lib/catalog-data-stack.ts`; the CDK tests.

## ADR-33: Change of mind by re-checking every chip

**Status.** Accepted (design decision, 2026-10-19). Replaces the `slot_changed` question and its follow-up round.

**Context.** In a live session, "and I want to look something for my wife" after "a gift for my brother" kept the men's shelf, and "what about some t shirts" while in Bags & Accessories kept the bags. The old design asked "did the shopper change an earlier choice, and which?" (`slot_changed`), then re-asked that one slot with the earlier sentences in the context. Both steps lost: `slot_changed` was often too unsure to trigger, and the earlier sentences pulled the re-asked answer back to the old value ("gift for my brother" kept brother at 0.59 against wife 0.13). Only one slot could change per sentence.

**Measurements** (live endpoint, 18 sentences: 9 change something, 9 do not):

| Design | Changes found | False changes |
|---|---|---|
| `slot_changed`, then re-ask with the earlier sentences | 2 of 9 | 0 |
| Re-ask every chip with the latest sentence and the chips | 7 of 9 | 0 |
| Re-ask every chip with the latest sentence alone | 4 of 9 | 2 ("for my wife" also moved the department) |

**Decision.**

- Every turn, in a parallel call, the committed slots (recipient, gender, department, category, and a budget from a band) are asked again with the latest sentence and the chips, without the earlier sentences.
- A confident answer (at its act threshold) that differs from a chip replaces it; several can change in one sentence. A new recipient brings its own gender. A new department clears the category, and the category round asks within the new department.
- An answer that differs but is only in the narrow band becomes one yes/no question ("Should I look in Women's Clothing instead?"); the shelf stays until the shopper answers, and yes applies the change and searches again.
- Not mentioned, or the same value, keeps the chip.

**Consequences.** One more decider call per turn while any chip is set, in parallel with the others. On the labeled set's change-of-mind turns: 6 of 8 switched, 2 asked (both right), none missed; no question's precision dropped. Negations ("not fragrance but some leather items") are still missed.

**Evidence.** `backend/agentcore-runtime/agent/questions.py` (`recheck_ids`, `recheck_text`), `policy.py` (`changed_slots`, `unsure_change`), `turn_pipeline.py`; the tests named for each case.

**Amendment (2026-10-20): a new person is a new goal.** In a live session, shirts for a father followed by "and now I want to purchase some gift for my mom" stayed on men's shirts: with four chips in the context the recipient re-check answered father 0.37 (mother 0.31), and when the change was finally taken, Men's Clothing and Shirts stayed, so nothing matched for women.

- The recipient is re-checked with every chip except its own: on 20 sentences, 20 right and no false change, against 17 right and 3 missed with its chip (mother 0.78 for the sentence above).
- A new recipient resets the gender, department, category and budget, and drops the earlier sentences, which were about the previous person (design decision). A stated amount in the same sentence is kept. The same sentence is then asked once more for the cleared details, with only itself and the new chips ("actually a dress for my sister instead" sets Women's Clothing, then Dresses). A yes to "Should I shop for your mother instead?" resets the same way.

## ADR-34: More results, cart removal and yes/no answers

**Status.** Accepted (2026-10-20). Amends ADR-12, ADR-17, ADR-23 and ADR-31.

**Context.** Live sessions on the synthetic catalog showed several gaps. Shoppers asked for more of the same results. They wanted to take items out of the cart. Yes/no questions had to be answered by voice or typed. With spoken replies off, the reply was not visible anywhere. A shelf of 36 items with presigned image URLs passed the AgentCore Runtime WebSocket's 64 KB frame limit, and the connection closed. Two reply cases went wrong: "can you go with number five" while No. 3 was open chose No. 3 (0.37 against 0.35), and "no, actually I like number five" to an offer only declined it.

**Decision.**

- **Show more.** `show_more` is an intent, offered only with a shelf. It adds the next 12 results of the latest search, up to 49 (the `best_fit` limit), and scrolls down. "Go back" returns to the shorter shelf. If the shelf is not from the latest search, the turn searches again.
- **Remove from the cart.** A `remove_from_cart` Gateway tool and Lambda deletes one cart line in `VoiceShop-Cart`. A `cart_item` question names the line ("remove the blouse from my cart"). Below its act threshold (0.85, provisional) the host asks "Should I remove ...?". One line in the cart and a confident intent removes it directly. "Empty my cart" always asks "Should I empty the cart?" first. An empty cart is reported. The × on a cart line sends `ui.cart_remove` and removes that line without asking the decider.
- **Yes/No buttons.** Every yes/no question shows Yes and No buttons. If the browser already allows the microphone, it opens automatically for the answer and stays on. No permission prompt appears without a click.
- **Voice off.** With spoken replies off, the chat shows the app's reply text.
- **Frame chunking.** A shelf over 12 items goes as `ui.shelf` with the first 12, then `ui.shelf_append` frames of 12 each, so every frame stays under 64 KB.
- **Pointing without the open product.** The pointing round sees the cards on screen but not the open product or a pending offer. Without them, "can you go with number five" chose No. 5 at 0.52.
- **"No" plus a new request.** A confident no with another request drops the offer and runs the request. A no whose request is the offered action itself ("no" to "go back?" read as `go_back`) still only declines.

**Consequences.** One more decider call when a removal names a line, and one more Lambda behind Gateway. `cart_item` needs a labeled set before its threshold leaves provisional. A shelf of more than 12 items arrives in several frames, and the frontend appends them in order.

**Evidence.** `backend/agentcore-runtime/agent/turn_pipeline.py` (`_show_more`, `_remove_from_cart`), `questions.py` (`cart_item_question`, `pointing_text`), `policy.py`, `ui_events.py` (`frames`), `agent/calibration.json`; `backend/backend-infrastructure/lambda/remove-from-cart/index.js`; `frontend/src/state/useShopSession.ts`, `components/AssistantPanel.tsx`.
