# Research Record

This file records the research behind the architecture of `sample-voice-ai-shopping-with-strands-decider-and-amazon-nova-sonic`. It summarizes each source in our own words and links it. Decisions built on this research are in [architecture-decisions.md](architecture-decisions.md).

This is a historical record. Some sections describe designs that were later replaced: the AgentCore Runtime Instances hosting (sections 4-5 and the Instances measurements, replaced by a SageMaker endpoint, ADR-22) and the Amazon Reviews 2023 catalog (section 8, replaced by a generated catalog, ADR-27). Those sections are marked as historical and kept because the measurements explain the decisions.

The facts below were collected during the planning research and have not been re-fetched while writing this file. Items marked **TO VERIFY** must be checked before the README or a blog post states them as fact. Measurements taken during implementation go in [Measurements](#measurements) at the end.

## Contents

1. [Origin: the voice gift-finder prototype](#1-origin-the-voice-gift-finder-prototype)
2. [Jev and "System One" decision models](#2-jev-and-system-one-decision-models)
3. [Strands Decider](#3-strands-decider)
4. [Strands Decider on AgentCore CPU microVMs](#4-strands-decider-on-agentcore-cpu-microvms)
5. [AgentCore Runtime Instances (GPU, historical)](#5-agentcore-runtime-instances-gpu)
6. [Earlier research notes](#6-earlier-research-notes)
7. [Amazon Nova 2.5 Sonic](#7-amazon-nova-25-sonic)
8. [Catalog dataset: Amazon Reviews 2023 (historical)](#8-catalog-dataset-amazon-reviews-2023)
9. [Template repo: voice in-app ordering](#9-template-repo-voice-in-app-ordering)
10. [Landscape: other voice shopping assistants](#10-landscape-other-voice-shopping-assistants)
11. [Measurements](#measurements)

## 1. Origin: the voice gift-finder prototype

Source: LinkedIn post by Joao Vitor Martins (jotamartins), https://www.linkedin.com/posts/jotamartins_one-more-experiment-with-typesafe-ais-jev-ugcPost-7516217932771741697-6yal/

- The post shows a near-real-time voice gift-recommendation prototype. Speech goes through Deepgram Flux, and TypeSafe AI's Jev makes the decisions over a catalog of more than 7,000 Shopify products.
- It claims about a third of a second per decision.
- What we took from it: a decision model can narrow a large catalog one sentence at a time while the shopper watches. A third of a second per decision implies GPU-class inference. Section 4 shows CPU is far slower.

## 2. Jev and "System One" decision models

Sources:
- Sanity glossary, "Jev (TypeSafe AI model)": https://www.sanity.io/glossary/jev-typesafe-ai-model
- LangChain blog, "Building a harness with Jev": https://www.langchain.com/blog/building-a-harness-with-jev

Summary:
- Jev is TypeSafe AI's "System One" decision model. It is discriminative, not generative: it answers a typed question with a fixed option set and returns a calibrated probability for each option in one forward pass. It does not write free text.
- The LangChain post reports Jev at up to 200x faster and 400x cheaper than LLMs on classification tasks. These are the vendor's and LangChain's figures, not ours.
- Why it matters here: a voice shopping turn is mostly classification (who is it for, which category, which item was pointed at). Per-option probabilities give a usable confidence signal, so the agent can act when sure and ask when not.
- Jev is a hosted third-party service, not an AWS service.

## 3. Strands Decider

Sources:
- TechCrunch, 2026-10-01, "Amazon releases its own Jev clone as decision models flood the web": https://techcrunch.com/2026/10/01/amazon-releases-its-own-jev-clone-as-decision-models-flood-the-web/
- Strands Agents blog, "Introducing Strands Decider": https://strandsagents.com/blog/introducing-strands-decider/
- GitHub: https://github.com/strands-labs/strands-decider

Summary:
- Strands Decider 2B comes from Strands Labs (TechCrunch credits it as Marc Brooker's project). It is open source under Apache-2.0 and uses a Qwen3.5-2B torso.
- Architecture: a pointer head of about 1M parameters replaces the language-model head, with a rank-16 LoRA on the torso.
- Question types: `noul` (probability of yes), `choice` (one of N with a probability per option), `score` (ordered rubric).
- Serving: `strands-decider serve <model>` exposes HTTP `POST /v1/systemone`. Exact request and response JSON schema: **TO VERIFY** in the repo README before implementation.
- The state is read once, so asking more questions about the same state costs little extra.
- Reported speed: median 115 ms per JevBench question on an RTX 3090 (p95 299 ms); 153 ms warm median on an Apple M3 Pro.
- Reported calibration: on unseen short tasks, answers given at confidence 0.9 or higher are right about 95% of the time.
- Reference model `strands-decider-2B-qwen3.5-v1-2610`: JevBench 0.779 (180 of 231), Brier score 0.280.
- Gemma 4 variants also exist (E2B, E4B, 12B, 26B-A4B), with a median of 102-245 ms per question on one H100 and about 9-51 GB of GPU memory for the weights. Not used here. The 2B model is enough for short typed questions and fits a 24 GB GPU.
- Qwen3.5 base is multimodal, and the server has a `--vision` option. It could resolve on-screen references from a screenshot. We keep it as a possible later enhancement, not part of the core design.
- Hugging Face repo IDs for the weights: **TO VERIFY**.

## 4. Strands Decider on AgentCore CPU microVMs

Sources:
- AWS Builder Center article: https://builder.aws.com/content/3KNqrNkiEtf7wTUzTufirwAVL7O
- Sample repo: https://github.com/Vivek0712/strands-decider-agentcore

Summary (measured by the article's author in us-east-1):
- Setup: AgentCore Runtime microVM with 2 vCPU and 8 GB memory, int8 build of the model.
- Cold start about 70 s. One yes/no question about 7.6 s. Three questions 20-23 s.
- The author concludes that tight interactive loops need a GPU host.
- What we took from it: CPU microVMs are fine for batch or offline decisions but are one to two orders of magnitude too slow for a per-sentence voice loop. This was the evidence for hosting the decider on a GPU (first AgentCore Runtime Instances, now SageMaker, ADR-22).

## 5. AgentCore Runtime Instances (GPU)

**Historical.** The decider ran here during the first spike. ADR-22 moved it to a SageMaker real-time endpoint, and ADR-26 removed the VPC this hosting required.

Sources:
- Developer guide, "How Runtime Instances work": https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html
- AWS News Blog, "Runtime Instances: persistent compute for production AI agents on Amazon Bedrock AgentCore": https://aws.amazon.com/blogs/aws/runtime-instances-persistent-compute-for-production-ai-agents-on-amazon-bedrock-agentcore/

Summary:
- Runtime Instances are AWS-managed EC2 instances in your own account. You set them up through a capacity provider that defines the OS, allowed instance types, VPC and subnets, EBS, and IAM.
- Supported GPU families: g4dn, g5, g6, g6e, gr6, g6f, gr6f, g7e, plus inf2. AgentCore provisions the GPU drivers, so a standard CUDA container image works.
- Networking is VPC only.
- They are invoked through the same `InvokeAgentRuntime` API as microVM runtimes. Each `runtimeSessionId` maps to an instance, and the first call for a session provisions it, which is slow. Sessions last up to 14 days.
- Pricing is standard EC2 plus an AgentCore management fee. This is a running cost while the instance exists, not scale-to-zero. Management fee rate: **TO VERIFY**.
- Regions at launch include US East (N. Virginia).
- Still to check during the spike: the idle timeout, whether `StopRuntimeSession` releases the instance and stops its charges, CloudFormation or CDK alpha support for the capacity provider, and the IAM the instance role needs (all **TO VERIFY**).
- **Confirmed during the decider spike:** AgentCore Runtime enforces a hard, non-adjustable 2 GB Docker image size cap (`bedrock-agentcore-limits.html`, "Maximum size for a Docker image in an AgentCore Runtime" = 2 GB, Adjustable: No). This invalidated the original plan to bake the decider's ~5 GB model checkpoint into its container image (ADR-14 revised; see that ADR and the Measurements section below).
- **Third-party reference deployment, found during the decider spike:** [builder.aws.com, "Running Strands Decider 2B on Amazon Bedrock AgentCore: microVMs vs GPU Instances"](https://builder.aws.com/content/3KJvK45IGyUNQwRd4dSOZQWKv8I/running-strands-decider-2b-on-amazon-bedrock-agentcore-microvms-vs-gpu-instances), published 2026-10-06, by an independent author. Deployed the same model to the same AgentCore Instances compute type (plus a SageMaker AI comparison) and hit, independently, the same 2 GB image cap and the same silent CPU-fallback trap this spike hit. Confirms, with real measured numbers from a different account/Region, that the working pattern is a slim (~180 MB) runtime image that pulls a prebuilt PyTorch/strands-decider venv and the model checkpoint from S3 at container startup, plus explicitly preloading the host's NVIDIA driver libraries (`/usr/lib64/libcuda.so.1`, `/usr/lib64/libnvidia-ml.so.1`) before importing `torch`. Also reports: the stock `strands-decider serve` HTTP server handles one request at a time per process (about 1.6–2.0 decisions/second on a T4 instance, roughly flat across 1/4/8 concurrent callers); routing accuracy was identical across every host tested (CPU or GPU): hosting changes speed and cost, not correctness; and `g5.xlarge` hit `InsufficientInstanceCapacity` in their Region that day, which is why this project's capacity provider allows more than one instance type as an escalation path rather than a single hard-coded type. Used in this spike to cross-check our own measured g6.xlarge numbers, not as a substitute for measuring them.

## 6. Earlier research notes

Source: earlier research notes, "Voice shopping with typed decisions on AWS", written while planning this sample. Credited here as an input to this design.

What the notes proposed:
- **Decision model:** OpenJev on an Amazon SageMaker real-time endpoint. The reason for SageMaker was specific to OpenJev. Bedrock Custom Model Import does not list the Qwen3.5 architecture, and its logprobs cover only the chosen token, while OpenJev needs a score for every option letter. Serving with vLLM, FP8 (about 29 GB) on ml.g6e or H100, with prefix caching.
- **OpenJev latency** (from its model card, one H100 at FP8): about 80 ms for short text, about 210 ms for a web page, about 120 ms for a new question on a cached page. The original Jev demo overlay showed 0.7-1.1 s per hosted Jev call, which includes network and hosting overhead.
- **OpenJev license:** weights are CC BY-NC 4.0 (non-commercial).
- **Session service:** AWS Fargate, with AgentCore Runtime listed as a possible alternative.
- **Data:** Amazon Berkeley Objects.
- **Per-sentence flow:** hear, understand, filter in code, choose, act. Thresholds: act at 0.8 or higher, ask below 0.5. At most 52 options per question.
- **Voice:** Amazon Nova 2.5 Sonic, 8-minute connection limit, launched October 5 in us-east-1, us-west-2, eu-north-1 and ap-northeast-1. Confirm the exact model ID in the Bedrock console.
- **Cheap fallback:** a Bedrock small model. Not adopted (see below).
- **Blog angle:** "System One / System Two agents on AWS".

What we kept and what we changed (details in the ADRs):
- Kept: the five-step flow, hard filters in code, the 0.8 / 0.5 thresholds, the option cap (we use 50 to leave room for a `none` option), Nova 2.5 Sonic and the 8-minute limit, the System One / System Two framing.
- Changed: Strands Decider 2B instead of OpenJev (Apache-2.0, AWS-native). With OpenJev gone, the reason for SageMaker is gone, so the decider runs on AgentCore Runtime Instances (ADR-1, ADR-2). AgentCore Runtime is the session host, which the notes listed as an alternative (ADR-3). Amazon Reviews 2023 replaces Amazon Berkeley Objects, which has no prices (ADR-5). No Nova Micro fallback: the sample is deployed only for development, testing and recording, and an uncalibrated, slower fallback would undercut the demo.

## 7. Amazon Nova 2.5 Sonic

Sources: earlier research notes (section 6), and the newest author repo `sample-travel-concierge-with-amazon-bedrock-agentcore-and-nova-sonic`, which uses `BidiNovaSonicModel` with model ID `amazon.nova-2-5-sonic`.

- Speech-to-speech over a bidirectional stream: 16 kHz audio in, 24 kHz audio out. It produces user transcripts and speaks replies, and supports tool use.
- Each connection has an 8-minute limit. This applies to the Sonic stream whatever service hosts the session (AgentCore Runtime or Fargate), so session continuation is needed either way.
- Model ID, pricing, and the quota for concurrent streams: **TO VERIFY** in the Bedrock console and pricing page.

## 8. Catalog dataset: Amazon Reviews 2023

**Historical. Replaced by the synthetic catalog (ADR-27, 2026-10-19).** VoiceShop no longer uses, downloads or ships any data from this dataset. The dataset declares no license, its product images are photos owned by brands and sellers, and redistribution is not granted. The notes below explain the earlier ADR-5, ADR-19 and ADR-20.

Source: https://amazon-reviews-2023.github.io/ (McAuley Lab, UC San Diego).

- 33 categories, 48.19M items overall. Amazon_Fashion has 825.9K items.
- Item metadata fields we use: `main_category`, `title`, `average_rating`, `rating_number`, `features`, `description`, `price` (float, USD at crawl time), `images` (thumb / large / hi_res with a variant such as `MAIN`), `videos`, `store`, `categories`, `details` (dict with Brand, Color, Size, Material and others), `parent_asin`, `bought_together`.
- Why this dataset: it has real USD prices, images, brands, categories and attribute details, which the demo needs for budget filtering and "the purple one" style references.
- Prices are from the crawl date and are not current. Some items have no price; curation drops them.
- Source file used for the v1 catalog asset (2026-10-18): https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Amazon_Fashion.jsonl.gz (224,299,124 bytes, sha256 `0b121c7494b0216ba3bf80adce9c79286fe08f14086966f841fe6716e1a24b73`). The same file is mirrored uncompressed on Hugging Face at `McAuley-Lab/Amazon-Reviews-2023`, `raw/meta_categories/meta_Amazon_Fashion.jsonl`.
- Eligibility measured on that file (826,108 records): the strict structured-field rule leaves 147 items, so the rule was relaxed per ADR-20. Under the ADR-20 rules, 5,237 items are eligible. Exclusions are counted under the first failing check: no price 775,859, no description 32,646, no color 4,990, no category 4,336, no gender 2,157, no material 771, no brand 93, no MAIN image 14, no title 5. There are no duplicate `parent_asin` values. Eligible items by category: Jewelry 2,402, Accessories 699, Scarves & Hats 435, Tops 426, Outerwear 277, Dresses 235, Sunglasses 216, Watches 197, Shoes 124, Handbags 120, Activewear 106. By gender: women 2,792, unisex 1,236, men 895, girls 215, boys 99.
- Release asset `voiceshop-catalog-v1.zip`: 5,236 items (1 image download failed), 116,132,067 bytes. Built by the since-removed `precurate.py` and hosted only in a private development bucket. It was never published. `catalogSize` defaulted to 5,000 because fewer than 7,000 items were eligible.
- **Usage terms.** They stayed TO VERIFY and were never confirmed. That is why the dataset was dropped (ADR-27).

Alternatives looked at (see ADR-5):
- Amazon Berkeley Objects: rich product images and metadata but no prices.
- Kaggle Amazon product scrapes, for example `lokeshparab/amazon-products-dataset`: unclear license and prices in INR.
- Kaggle fashion product image datasets: no prices.

## 9. Template repo: voice in-app ordering

Source: `guidance-for-voice-ai-in-app-ordering-using-amazon-bedrock-agentcore` (same authors).

Patterns reused:
- Four-section layout: Backend Infrastructure, AgentCore Gateway, AgentCore Runtime, Frontend.
- The frontend opens a SigV4-presigned WebSocket to AgentCore Runtime (`wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws`). The runtime verifies the Cognito JWT on the first message.
- Strands `BidiAgent` with `BidiNovaSonicModel` bridges the browser audio to Nova Sonic.
- AgentCore Gateway (MCP, IAM auth) exposes backend endpoints as tools, discovered at runtime with `list_tools_sync()`.
- Cognito User Pool and Identity Pool, Amplify hosting, CodeBuild ARM64 builds pushed to ECR, root scripts `deploy-all.sh`, `cleanup-all.sh`, `preflight-check.sh`, `status.sh`, `deployment-state.sh`.
- Measured cost: about $78.62 per month for 1,000 voice orders, about 88% of it Nova Sonic output tokens. That gives about $0.07 of Sonic cost per 5-minute session, which we use as a starting estimate.

## 10. Landscape: other voice shopping assistants

- SoundHound Sales Assist (shown at MWC 2026).
- Salesforce Agentforce Voice for Retail.
- Shopify, BigCommerce and Bloomreach AI shopping assistants.
- AWS Solutions, Drive-thru voice AI using Amazon Bedrock: https://docs.aws.amazon.com/solutions/drive-thru-voice-ai-using-amazon-bedrock/ . A shopping session is structurally close to a drive-thru order, where the shopper picks from a fixed catalog by voice.

Conclusion: voice shopping by itself is table stakes. The difference in this sample is a calibrated decision model and confidence-driven conversation. The assistant acts when the model is sure, asks one short question when it is not, and says so when nothing matches. As far as we found, none of the products above use a calibrated per-option decision model this way. This is based on their public descriptions, not on testing them.

## Measurements

To be filled in during implementation. Record date, Region, instance type, image tag, and method for each.

| Measurement | Value | Date / method |
|---|---|---|
| Decider understand round (3 questions), warm p50 / p95, g6.xlarge | **server 138.8 / 139.8 ms**; client 321.8 / 401.2 ms | 2026-10-18, us-east-1, `measure-latency.py`, 50 calls, image `930e6d5-gpu-field`, GPU NVIDIA L4 |
| Decider understand round, warm p50 / p95, g5.xlarge | TBD | |
| Decider `best_fit` over 50 options, p50 / p95 | **server 244.0 / 247.0 ms**; client 409.2 / 437.9 ms | same run, 50 calls |
| Decider `pointed_item` over 12 options, p50 / p95 | **server 84.5 / 86.2 ms**; client 256.3 / 335.8 ms | same run, 50 calls |
| `InvokeAgentRuntime` overhead (client round trip minus server `latency_ms`) | 169.5 / 262.3 ms p50 / p95, **from a laptop outside AWS** (includes internet round trip) | same run, 150 calls. superseded by the in-Region row below |
| `InvokeAgentRuntime` overhead **in-Region** | **131.4 / 199.6 ms** p50 / p95. Client totals: understand 275 / 349, `best_fit`/50 376 / 397, `pointed_item`/12 218 / 290 ms. Server side unchanged: 140 / 246 / 87 ms p50 | 2026-10-18, us-east-1, CodeBuild project `VoiceShop-DeciderMeasure` (same Region, no VPC), 150 calls, image `930e6d5-gpu-field`. Cold start for a new session id 228 s |
| Instance provisioning time (first call per session id) | 209 s (new session id, cold instance, empty volume) | same run; breakdown from logs: venv pull+extract ~87 s, checkpoint ~6 s, base model ~26 s, model load ~6 s, rest is instance launch + image pull |
| `search_catalog` retrieval, handler p50 / p95, 5,000 items, `limit=200`, availability filter only | **262 / 342 ms** (Titan query embedding 133 / 173, kNN + counts 83 / 121, presigning 200 thumbnails 34 / 74) | 2026-10-18, us-east-1, `backend/catalog-data/scripts/measure-retrieval-latency.py`, 50 calls, Lambda 1,024 MB ARM64, handler `timingsMs`. Gateway not included |
| `search_catalog`, `limit=200`, category + gender + budget filters | **244 / 298 ms** (embed 131 / 182, kNN 82 / 107, presign 22 / 54) | same run, 50 calls, 1,819 matched |
| `search_catalog`, `limit=50` | **185 / 220 ms** (embed 132 / 161, kNN 48 / 65, presign 5 / 6) | same run, 50 calls |
| `search_catalog` Lambda invoke overhead (client minus handler) | 85 / 142 ms p50 / p95, **from a laptop outside AWS** | same run. First call after deploy: client 1,606 ms (Init Duration 328 ms) |
| **SageMaker endpoint (ADR-22): decider rounds, in-Region, p50 / p95** | understand (3 questions) **140 / 146 ms** (server 131), `best_fit`/50 **260 / 263 ms** (server 251), `pointed_item`/12 **89 / 93 ms** (server 81) | 2026-10-18, us-east-1, ml.g6.xlarge (NVIDIA L4), image `sm-6e7ab7bdcb7a`, `VoiceShop-DeciderMeasure` CodeBuild, 50 calls per round |
| **SageMaker InvokeEndpoint overhead, in-Region** | **8.8 / 9.9 ms** p50 / p95 (vs 131 / 200 ms for AgentCore InvokeAgentRuntime) | same run, 150 calls |
| SageMaker endpoint creation | CREATE_COMPLETE in about 15 minutes (6.4 GB image + 4.8 GB model data); first request 281 ms | same day |
| `search_catalog` through AgentCore Gateway (MCP, SigV4), `limit=200` | client **472 / 556 ms** p50 / p95 vs handler 246 ms: Gateway + invoke overhead **219 / 255 ms**, from a laptop. A direct Lambda invoke from the same laptop added 85 ms, so Gateway itself adds ~130 ms | 2026-10-18, `backend/agentcore-gateway/test-client/gateway-check.py`, 30 calls. Re-measure in-Region from the session host |
| `StopRuntimeSession` releases instance and stops charges? | **Not immediately.** HTTP 200, but the instance kept running. About 17 min after the call (18 min after the last invoke, consistent with the 900 s idle timeout), AgentCore stopped it, then AutoScaling terminated it. The 112 GB root volume was deleted, and the 20 GB persistent volume was kept (`available`, billed as EBS) | 2026-10-18, one spike instance, see "Instances lifecycle" below |
| Sonic end of speech to final user transcript | **845 ms median** (598–1,005, n=5) from the last streamed frame; about **980 ms** from the real end of speech (Polly clips carry ~135 ms of trailing silence). `endpointingSensitivity` HIGH 867 / MEDIUM 854 ms: no measurable effect | 2026-10-18, us-east-1, `amazon.nova-2-5-sonic`, strands-agents 1.61.1 `BidiAgent` + `BedrockNovaSonicModel`, Polly neural 16 kHz PCM streamed at real-time pace in 64 ms frames, `backend/agentcore-runtime/scripts/sonic-check.py` |
| Two concurrent Sonic streams from one process | **Allowed**: both transcribed correctly | same run |
| First UI update after shopper stops talking, p50 / p95 | TBD | `FirstUiLatencyMs` |
| Shelf refresh, p50 / p95 | TBD | `ShelfLatencyMs` |
| Monthly cost at the README's assumptions | TBD | Cost Explorer + pricing pages |

### Retrieval latency (2026-10-18)

The first run at the design default (`limit=200`, Lambda 512 MB) measured **789 / 1,103 ms** handler p50 / p95, against a 300 ms budget. Presigning the 200 thumbnails took 494 ms p50, because `getSignedUrl` builds a full S3 command middleware stack for every URL. Two implementation changes fixed it without changing the design:

1. `lambda/shared/aws.js` now signs URLs with one reusable `SignatureV4` backed by Node `crypto`, which caches the signing key per day. Presigning fell to 82 ms, and the handler to 314 ms.
2. `search-catalog` now has 1,024 MB instead of 512 MB, for more CPU share. Presigning fell to 34 ms, and the handler to the 262 ms above.

`search_catalog` now also returns `timingsMs` (`embed`, `search`, `presign`, `total`), an additive output field for the `ui.debug` overlay.

What is left: the Titan V2 query embedding (~130 ms) is now the largest fixed cost. Unfiltered p95 (342 ms) is slightly over budget. Gateway adds about 130 ms (measured later, row above). Retrieval runs in parallel with the understand round and starts on stable partial transcripts (ADR-24). These numbers are for the earlier 5,000-item fashion catalog; re-measure on the synthetic catalog.

### Instances lifecycle, quotas and pricing (2026-10-18, historical)

This describes AgentCore Runtime Instances, which no longer host the decider (ADR-22).

- **Idle stop.** Instances are not billed until cleanup. After about 15 minutes with no invocations, AgentCore stops the instance, and AutoScaling then terminates it (CloudTrail `TerminateInstances` by AutoScaling at 19:29:28 UTC). This matches the capacity provider's `InstanceLifecycleConfiguration.idleInstanceTimeout` (default 900 s, range 60–1,209,600 s). The run did not separate the effect of `StopRuntimeSession` from the idle timeout: termination came at the idle-timeout mark, not right after the call. The runtime's own lifecycle is `idleRuntimeSessionTimeout` 900 s and `maxLifetime` 28,800 s; on Instances both can go up to 14 days, and the runtime's `maxLifetime` must not exceed the capacity provider's. Sources: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html, https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_InstanceLifecycleConfiguration.html
- **Persistent volumes outlive the instance.** The per-session 20 GB volume is kept after termination and bills at EBS rates. Nine of them (180 GB) had piled up from earlier spike sessions. A resource policy denies `ec2:DeleteVolume` on them, so they can only be removed with `bedrock-agentcore delete-capacity-provider-session` (session id from the volume tag). That call deleted all nine within a minute. Volumes from the previously deleted capacity provider were gone, so deleting the capacity provider appears to delete its volumes (the docs say deleting a session or capacity provider removes the volumes). `cleanup-all.sh` now deletes these sessions first, while the capacity provider still exists.
- **Warm restart from a kept volume.** For the same session id after AgentCore terminated its idle instance, the first call took **127.5 s**, against 194.3 s for a brand-new session id (same run, 2026-10-18). The next call was 0.6 s. The kept volume saves the checkpoint and venv downloads (~67 s). Instance launch, image pull and model load still take about 2 minutes, so a pool session that went idle is not warm.
- **Hidden from listings.** AgentCore-managed instances and volumes do not appear in `describe-instances` / `describe-volumes` listings, filtered or unfiltered. They only appear when queried by id. CloudTrail (`RunInstances` / `CreateVolume` with `invokedBy: bedrock-agentcore.amazonaws.com`) is the reliable way to find them.
- **Session id.** Requests require 33–256 characters; the response header documents a maximum of 100 and the pattern `[a-zA-Z0-9][a-zA-Z0-9-_]*`. Use 33–100 characters of that pattern. Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html
- **Pricing.** EC2 is billed to this account at EC2 rates, plus an AgentCore management fee that is a percentage of the On-Demand price, per instance-hour. The fee is reduced to 7.8% for G-series families such as g6; the page's worked example uses 12%. EBS persistent volumes bill at standard rates, including while a session is stopped. Source: https://aws.amazon.com/bedrock/agentcore/pricing/
- **Quotas.** 20 agents per capacity provider session; 100 MB maximum request/response payload; 15-minute request timeout; 25 TPS for new sessions. Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html

### `best_fit` distributions and the `no_match` rule (2026-10-18)

Method: a script (`best-fit-distributions.py`, written for the earlier catalog and since removed; the current method is `backend/agentcore-runtime/eval/catalog`), 22 shopper sentences. For each sentence, the deployed `search_catalog` returns up to 200 hits (with the committed filters) and the top 50 by score become `best_fit` options labelled `title | brand | color | $price`. Same warm g6.xlarge session as the latency run.

- **The picks are sensible, but the distributions are flat.** Top probability median is 0.116 for in-catalog requests (range 0.063–0.267) and 0.104 for requests the catalog cannot satisfy. `confidence` never exceeded 0.25, so `best_fit` never gets near `ACT_THRESHOLD` 0.8.
- **The design's `no_match` rule (top < 0.5 and top < 2× second) does not work.** It fired on 12 of 16 in-catalog requests (false `no_match`). Ratio medians were 1.54 (in catalog) vs 1.42 (not in catalog): no separation.
- **Variant A: an explicit `none_of_these` criterion.** Its probability was ≤ 0.127 on every in-catalog request, and 0.304 (laptop charger), 0.294 (kitchen blender) and 0.142 (tuxedo with tails) on the three requests the catalog truly cannot satisfy. Three "expected no match" requests were in fact satisfiable (wedding dresses, steel-toe boots and a diamond ring exist in the catalog; the budget was not filtered), and they scored 0.064–0.089. A threshold of about 0.135 separates them with a thin margin.
- **Variant B: a separate `noul` "is any candidate a good match" question** (top 12 candidates in the state). It scored 0.60–0.83 in catalog and 0.50–0.52 for charger and blender, but 0.74 for the tuxedo. It separates less cleanly than variant A.
- The raw output for the three variants was not committed.

### SageMaker endpoint (ADR-22, 2026-10-18)

- **Overhead.** Moving the decider to SageMaker cut the per-call overhead from 131 ms (AgentCore) to 8.8 ms p50. Client-side rounds are now within about 9 ms of the decider's own time: understand 140 ms (was 275), `best_fit` 260 ms (was 376), pointing 89 ms (was 218). Server-side times match the AgentCore measurements, as expected for the same container code and model.
- **`best_fit` distributions.** The same script (`--variant none_option`) against the endpoint gave the same `none_of_these` probabilities as on AgentCore (≤ 0.127 in catalog, 0.142–0.304 for absent requests), so ADR-21 and `NO_MATCH_THRESHOLD` 0.135 carried over (historical; now 0.35, see the ADR-21 amendment).
- **VPC endpoints.** Flow logs from the endpoint's creation (image pull, model download, start) showed no traffic to the ECR, STS or CloudWatch Logs interface endpoints. SageMaker pulls the image outside the VPC, and the model runs with network isolation. The VPC-attached catalog curation CodeBuild project does use the Logs endpoint (build log shipping). ECR api, ECR dkr and STS were removed from `VoiceShop-NetworkStack` (about $44/month). The endpoint kept serving afterwards. On 2026-10-19 the whole VPC was removed (ADR-26): the endpoint runs with network isolation and no `VpcConfig`, and the curation job no longer runs in a VPC.
- **Quota.** A new account's `ml.<type> for endpoint usage` quota may be 1, so auto-scaling is configured but capped at one instance until the quota is raised.

### Nova 2.5 Sonic (2026-10-18)

- **Model id** `amazon.nova-2-5-sonic` (ACTIVE in us-east-1; speech and text in, speech and text out). It matches the `sonicModelId` default.
- **End of speech to final user transcript: ~850 ms.** The final user transcript block arrives at the same moment as Sonic's first response output, so this is Sonic's own end-of-turn detection, not transport delay. `turnDetectionConfiguration.endpointingSensitivity` (documented pauses of 1.5 / 1.75 / 2 s for HIGH / MEDIUM / LOW) did not change it measurably for these short, clearly ended sentences. Partial user transcript deltas arrive 600–760 ms after end of speech for short sentences, and during speech for longer ones. Sonic also finalizes clause by clause: in a two-sentence utterance, the first sentence's block arrived before the speaker finished.
- **Concurrency.** Two simultaneous streams from one process both worked, so make-before-break session continuation is possible.
- **SDK changes since the template.** strands-agents 1.61.1 promotes bidi out of `experimental` (`strands.bidi.BidiAgent`, `strands.bidi.models.BedrockNovaSonicModel`; the old path is a deprecated alias). It renames events (`bidi_transcript_delta` / `_block`, `bidi_audio_delta`, `bidi_barge_in`) and has built-in proactive connection restart (`ConnectionConfig.restart_after_s`, default 420 s) plus a `bidi_connection_restart` event. The session host uses that built-in restart for the 8-minute limit and reports it as `ui.status continuation`. Separately, Sonic closes a stream after 55 s without audio; the bridge reopens it on the next mic frame or typed sentence.
- **Impact on the latency budget.** With ~850 ms (not 350 ms) before the pipeline can start on a final transcript: first UI ≈ 850 + 262 (retrieval, the slower parallel step) + 30 ≈ 1.14 s (goal 1.0 s); shelf ≈ 850 + 262 + 5 + 260 + 30 ≈ 1.41 s (goal 1.3 s).

### Historical: decider container startup on AgentCore Instances (2026-10-16 to 2026-10-18)

This applies to the AgentCore Runtime Instances hosting, which ADR-22 replaced. Kept for anyone deploying a GPU container there.

**What was tried first.** Eight attempts in a row failed with the same generic platform message ("Container exited before becoming ready" / "The agent process failed to start") and no application log output. Along the way, these changes were made and kept because each was correct on its own: the two-process design (a supervisor plus a FastAPI adapter in front of `strands-decider serve`) became one process that calls `strands_decider.server.create_app` directly; `/ping` returned 200 for `HealthyBusy` (it had returned 503, which the platform reads as failed); and `HF_HUB_CACHE` was set next to `HF_HOME` so the offline Hugging Face lookup finds the S3-pulled cache. None of them fixed the startup failure. A record written at the time concluded that only an AWS Support case could help. That was wrong: the cause was found without one.

**What fixed it,** in order:

1. **Root cause of "Container exited before becoming ready": the `nvidia/cuda` base image.** The identical trivial hello-world app failed every time on `nvidia/cuda:12.4.1-runtime-ubuntu22.04` (1.48 GB) and booted on the first try on `python:3.12-slim` (60 MB), with the capacity provider, VPC, IAM and runtime unchanged. The decider image was rebased on `python:3.12-slim` (84 MB). No CUDA base is needed: the host driver is mounted at `/usr/lib64` and the torch cu126 wheels bundle the CUDA runtime.
2. **Why no logs were ever seen:** the runtime had no `APPLICATION_LOGS` vended-logs delivery. Adding one made container stdout visible.
3. **Upload bugs in `deploy-all.sh`:** the venv tarball went to the bucket root instead of `venv/`, and the base-model cache upload followed the cache's snapshot symlinks, storing 12.8 GiB instead of 4.3 GiB. Both fixed.
4. **Readiness deadline:** AgentCore Instances gives up about 120 s after container start, but first-boot setup took about 2 minutes before the model loaded. The entrypoint now served `/ping` immediately (HTTP 200, `HealthyBusy` while loading) and loaded in a background thread.
5. **Route:** `create_app` serves `POST /v1/systemone`, not `/invocations` (HTTP 404). The entrypoint mapped one to the other and added a `gpu` field to each response. The SageMaker container (`serve.py`) still does this.
6. **Client:** `InvokeAgentRuntime` must send `contentType="application/json"`; without it the decider returns HTTP 422 for every request.

**Against the goal (~250 ms per round).** Server-side, `g6.xlarge` met it: understand round 139 ms p50, `pointed_item` 85 ms, `best_fit` over 50 options 244 ms. The in-Region `InvokeAgentRuntime` overhead (131 ms p50) was what pushed rounds over budget, not the GPU. That overhead, the cold starts and the lack of load balancing led to ADR-22, where the same container code measures 8.8 ms overhead.
