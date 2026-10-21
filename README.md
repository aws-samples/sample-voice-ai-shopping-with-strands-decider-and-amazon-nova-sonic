# VoiceShop: voice shopping with Strands Decider and Amazon Nova Sonic

VoiceShop is a visual-first voice shopping sample. The shopper sees a shelf of products and talks or types: "a gift for my mom under eighty dollars", "show me watches", "number 3", "add it to my cart". Each sentence becomes a few typed questions that Strands Decider 2B answers with calibrated per-option probabilities. Code applies the hard rules (budget, department, category, gender, availability). The assistant acts when the decider is sure and asks one short question when it is not. Amazon Nova 2.5 Sonic handles speech in and, optionally, speech out. The product catalog is generated for this sample, so it contains no third-party product data. This is a reference sample, not a production retail system: no payment is ever taken.

## Architecture

![VoiceShop architecture](assets/architecture-diagram.png)

- **Frontend:** React on AWS Amplify Hosting. Sign-in with Amazon Cognito (the demo user is `AppUser`). Temporary AWS credentials come from a Cognito Identity Pool.
- **Session host:** Python (FastAPI, Strands `BidiAgent` with Amazon Nova 2.5 Sonic) on Amazon Bedrock AgentCore Runtime, one microVM per shopping session. The browser connects over a SigV4-signed WebSocket.
- **Decision model:** Strands Decider 2B on an Amazon SageMaker real-time endpoint (`voiceshop-decider`): ml.g5.xlarge by default (`--decider-instance-type`), at least 1 instance, auto-scaling up to `--decider-max-instances`, network isolation.
- **Tools:** AgentCore Gateway (MCP, IAM auth) in front of five AWS Lambda functions: `search_catalog`, `get_product_detail`, `add_to_cart`, `remove_from_cart`, `place_order`. A sixth function, `VoiceShop-CatalogIndexWriter`, runs only at deploy time and is not a Gateway tool.
- **Memory:** AgentCore Memory for shopper preferences across visits.
- **Data:** Amazon DynamoDB tables `VoiceShop-Products`, `VoiceShop-Sessions`, `VoiceShop-Cart`, `VoiceShop-Orders`.
- **Search:** Amazon OpenSearch Serverless vector search, with embeddings from Amazon Titan Text Embeddings V2.
- **Images:** a private Amazon S3 bucket; the browser gets presigned URLs.
- **Catalog load:** one AWS CodeBuild job (`VoiceShop-CatalogCuration`) loads the catalog zip from this repository; a small function inside the VPC, `VoiceShop-CatalogIndexWriter`, writes the search index.
- **Private search.** The search collection accepts requests only through its VPC endpoint, and each must be SigV4-signed by a role named in a data access policy. Only the two functions that call it run in the VPC, which has one Availability Zone and no internet route; use two or more zones in production. The decider container has no network access at all ([ADR-32](docs/architecture-decisions.md#adr-32-private-search-collection), [ADR-26](docs/architecture-decisions.md#adr-26-no-vpc)).

## Prerequisites

- An AWS account and credentials for `us-east-1` (set `VOICESHOP_REGION` to use another Region where every service is available).
- Amazon Bedrock model access to **Amazon Nova 2.5 Sonic** and **Amazon Titan Text Embeddings V2**. Claude and Stability AI models are needed only to regenerate the catalog ([generator](backend/catalog-data/README.md#generator)).
- SageMaker quota **"ml.g5.xlarge for endpoint usage"** (Service Quotas; for another `--decider-instance-type`, that type's quota) of at least `--decider-max-instances` (default 1). New accounts often start at 0. `deploy-all.sh` checks the quota before it deploys anything.
- The AWS CDK bootstrapped in the account and Region (`npx cdk bootstrap`).
- AWS CLI v2, Node.js 22, Python 3.12. Docker is not required: CodeBuild builds the container images.

## Deploy

```bash
./deploy-all.sh --user-email you@example.com                            # first deploy
./deploy-all.sh --user-email you@example.com --decider-max-instances 2   # allow the decider to scale to 2 instances
```

The script runs two chains side by side. The decider chain (its ECR repository and infrastructure, the one-time model upload in CodeBuild, the image build, the SageMaker endpoint) runs in the background and logs to `deploy-decider.log`. The main chain deploys the backend infrastructure, the catalog, the Gateway, the session host (infrastructure, image build, runtime) and the frontend. The script then waits for both and pre-warms the endpoint. Re-running it is safe. Flags:

| Flag | Meaning |
|---|---|
| `--user-email <email>` | Email of the demo user. Required on the first deploy |
| `--user-name <name>` | Display name of the demo user (default `AppUser`) |
| `--skip-catalog` | Skip loading the catalog (step 2) |
| `--decider-instance-type <type>` | Decider GPU instance, without `ml.` (default `g5.xlarge`; `g6.xlarge`, `g6e.xlarge` also work) |
| `--decider-max-instances <n>` | Upper bound for decider auto-scaling (default 1); must not exceed your quota |

A fresh run took about 31 minutes (measured 2026-10-21): the decider chain (model upload, image build about 16 minutes, endpoint about 9 minutes) runs in parallel with the rest, which takes about 27 minutes. With `cleanup-all.sh --keep-decider-image` the image survives a cleanup, and the next deploy skips the image build, so it is faster. Use a credentials profile that refreshes itself, for example AWS IAM Identity Center or `credential_process`: `AWS_PROFILE=<profile> ./deploy-all.sh ...`.

When it finishes, the script prints the Amplify URL. Sign in as `AppUser`. The first sign-in uses the temporary password from the Cognito invitation email sent to `--user-email`.

## Using it

- **Talk or type.** Allow the microphone and speak, or type in the search bar.
- **Voice on / Voice off.** Spoken replies are off by default; the header toggle turns them on. Yes/no confirmations are spoken either way.
- **Pointing.** Every card shows its number. Say "number 5", "the second one" or describe it. When the assistant is unsure, it asks "Do you mean No. 5, ...?". Tapping a card always works.
- **Chips.** Committed choices appear as chips with their confidence. Click a chip to remove it. Change your mind at any time ("actually it's for my dad"); a new recipient clears the earlier details.
- **More results and moving around.** "Show me more" adds the next 12 results, up to 49. "Scroll down" and "back to the top" move the page. "Close it" closes the open product.
- **No match.** The closest items stay on screen, and the assistant offers to drop one filter. Say "yes" to widen.
- **Yes/no questions.** They show Yes and No buttons. If the browser already allows the microphone, it opens by itself so you can answer by voice.
- **Cart.** "Add it to my cart". Remove a line by voice ("remove the blouse from my cart") or with its ×. "Empty my cart" asks first.
- **Orders.** "Place my order" always asks "Should I place the order?" first. If the cart is empty, the assistant says so. An order is a DynamoDB record; nothing is charged.
- **Voice off.** With spoken replies off, the chat shows the reply text instead.
- **Behind the scenes.** Press **D** or use **Debug overlay** in the footer to see each sentence's questions, answers, counts and timings. **Pop out** moves the panel to a separate window.

## Cost

The decider endpoint is the main running cost. It keeps at least one ml.g5.xlarge running while deployed, about $1.41 per hour on-demand in us-east-1 (ml.g6.xlarge: about $1.13), whether or not anyone is shopping. OpenSearch Serverless also bills a minimum while the collection exists, and the search VPC's two interface endpoints (Bedrock runtime, OpenSearch Serverless) bill per hour. Run `./cleanup-all.sh` when you finish testing or recording.

## Cleanup

```bash
./cleanup-all.sh --dry-run   # show what would be deleted
./cleanup-all.sh             # delete everything (asks to confirm; --force skips the prompt)
./cleanup-all.sh --keep-decider-image   # delete everything except the decider image, for a faster redeploy
```

### Redeploying faster: keep the decider image

Each fresh deploy builds the decider container image from source in AWS CodeBuild: it pulls the AWS Deep Learning Containers base image from Amazon ECR Public, installs Strands Decider, PyTorch and a compiled CUDA extension, and pushes the result to your private Amazon ECR repository. That takes about 16 minutes. If you plan to deploy again, keep the image:

```bash
./cleanup-all.sh --keep-decider-image   # everything is deleted except VoiceShop-DeciderImageStack
./deploy-all.sh                          # later: the image is found in ECR and the build step is skipped
```

- **What is kept:** only `VoiceShop-DeciderImageStack`, the ECR repository with the built image (13.5 GB compressed). ECR storage for it costs about $1.35 per month; nothing else runs or bills.
- **What is saved:** the image build. The decider steps run in parallel with the rest of the deploy, so the whole deploy gets only a few minutes shorter (from about 31 to about 28 minutes in the 2026-10-21 run, when the other steps become the longer path), and the image is not rebuilt or re-pulled from ECR Public.
- **When it is rebuilt anyway:** the image tag is a hash of `backend/decider-runtime/container/` (Dockerfile, requirements, `serve.py`). If you change any of them, the next deploy builds and uses a new image.
- **Removing it later:** run `./cleanup-all.sh` without the flag.
- **Why the image is not in the repository:** it is far larger than Git allows, SageMaker runs images only from a private ECR repository in your account and Region, and building from source keeps every layer visible and auditable.

It deletes every VoiceShop stack in reverse deployment order, then the log groups and local outputs that no stack owns. The shared CDK bootstrap stack is left alone. A full cleanup took about 44 minutes (measured 2026-10-20 and 2026-10-21). About 24 of them were `VoiceShop-LambdaStack`, while AWS released the network interfaces of the two VPC functions. Deleting the decider endpoint stack can also take 15 minutes or more, so use a refreshing credentials profile here too.

## Troubleshooting

- **No mic audio with a Bluetooth headset on macOS.** The browser may receive silence until the device is selected again. Re-select the headset as the input in the system sound settings or the browser, or use the built-in microphone.
- **"InsufficientInstanceCapacity" when the endpoint is created.** AWS has no GPUs of that type free in the Region at the moment. Re-run with another type, for example `--decider-instance-type g6.xlarge`, or retry later.
- **A step seems to hang for many minutes on a Mac.** The laptop went to sleep. `deploy-all.sh` and `cleanup-all.sh` keep a Mac awake with `caffeinate` while they run, but closing the lid on battery still sleeps it; keep it open or on power.
- **Credentials expire during a long run.** Use a profile that refreshes itself (IAM Identity Center or `credential_process`) and re-run the script; it continues where stacks are already deployed.
- **`--decider-max-instances` exceeds your quota.** Request an increase for "ml.<type> for endpoint usage" in Service Quotas, or use a lower value.
- **The frontend says config values are missing.** The backend stacks are not deployed yet. Run `deploy-all.sh`.

## Repository layout

| Path | What it is |
|---|---|
| `backend/backend-infrastructure/` | CDK: the search VPC, DynamoDB, S3, OpenSearch Serverless, the five tool Lambdas and the index writer, Cognito |
| `backend/catalog-data/` | The catalog zip, its generator and the loader job ([README](backend/catalog-data/README.md)) |
| `backend/decider-runtime/` | Strands Decider container and the SageMaker endpoint ([README](backend/decider-runtime/README.md)) |
| `backend/agentcore-gateway/` | AgentCore Gateway with the Lambda targets ([README](backend/agentcore-gateway/README.md)) |
| `backend/agentcore-runtime/` | Session host (`agent/`), its CDK app, evaluations (`eval/`) and a text test client |
| `frontend/` | React app and the Amplify stack ([README](frontend/README.md)) |
| `codebuild-scripts/` | Starts a CodeBuild project and waits for it |
| `docs/` | Decisions, tuning notes and research |
| `deploy-all.sh`, `cleanup-all.sh` | Deploy and tear down everything |

## Documentation

- [Architecture decisions](docs/architecture-decisions.md)
- [How we tuned it: adapting Strands Decider without training](docs/how-we-tuned-it.md)
- [Catalog updates in production](docs/catalog-updates-in-production.md) (proposed, not implemented)
- [Catalog generator and loader](backend/catalog-data/README.md)
- [Research record](docs/research.md)

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file. Strands Decider weights are Apache-2.0.
