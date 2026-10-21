# Strands Decider on a SageMaker real-time endpoint

The decision model behind every sentence: Strands Decider 2B (Qwen3.5-2B base plus the strands-decider checkpoint) on the SageMaker endpoint `voiceshop-decider` (ADR-22).

| Piece | What it is |
|---|---|
| `container/` | Multi-stage x86_64 image: CUDA/PyTorch environment baked in, `serve.py` serves `/ping` (200 once the model is loaded) and `/invocations` (→ the decider's `/v1/systemone`). Built by CodeBuild; tagged `sm-<content hash>` |
| `cdk/` | `VoiceShop-DeciderInfraStack` (ECR, CodeBuild, model bucket) and `VoiceShop-DeciderEndpointStack` (model with network isolation and no VPC (ADR-26), endpoint, auto-scaling, the in-Region `VoiceShop-DeciderMeasure` project) |
| `scripts/` | `prewarm.py` (deploy check), `measure-latency.py` (run in-Region by the `VoiceShop-DeciderMeasure` CodeBuild project), the `best_fit` / `no_match` study behind ADR-21 used a script that was written for the earlier catalog and has been removed; `backend/agentcore-runtime/eval/catalog` is the current method |

The model files (~4.8 GB) are uploaded once by `deploy-all.sh` to the model bucket's `model/` prefix, which SageMaker mounts read-only at `/opt/ml/model`.

## Scaling and cost

- The endpoint keeps **one ml.g5.xlarge always running** by default while deployed (about $1.41/hour on-demand in us-east-1, ~$1,028/month; ml.g6.xlarge is about $1.13/hour, choose it with `--decider-instance-type g6.xlarge`), so no shopper waits for a cold start. Run `cleanup-all.sh` when you are done.
- It scales out on invocations per instance (target 120 per minute, about half of one instance's capacity).
- The scale-out cap defaults to **1**. Raise it with `./deploy-all.sh --decider-max-instances <n>`, after checking that your account's SageMaker quota **"ml.g5.xlarge for endpoint usage"** (Service Quotas, code `L-1928E07B`) is at least `<n>`. Other instance types use their own quota. New accounts often start at 0 or 1; request an increase in the Service Quotas console first. `deploy-all.sh` refuses a value above your quota.
- Other knobs live in `cdk/cdk.json`: `deciderInstanceType`, `deciderMinInstances`, `deciderInvocationsPerInstanceTarget`.

## Measured (2026-10-18, us-east-1, ml.g6.xlarge, in-Region, p50)

Understand round (3 questions) 140 ms · `best_fit` over 50 products 260 ms · `pointed_item` over 12 89 ms · InvokeEndpoint overhead 8.8 ms. Details in [docs/research.md](../../docs/research.md#sagemaker-endpoint-adr-22-2026-10-18).
