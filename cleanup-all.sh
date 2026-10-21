#!/usr/bin/env bash
# Destroys the entire VoiceShop solution: every VoiceShop CloudFormation stack,
# then the resources AWS services create outside those stacks, so nothing billable
# or stale is left behind.
#
# Stacks are deleted in the exact reverse of their deployment order (DEPLOY_ORDER
# below, which matches deploy-all.sh):
#   frontend -> agentcore-runtime (runtime, infra) -> agentcore-gateway ->
#   decider-runtime (SageMaker endpoint, then infra) -> catalog-data
#   -> backend-infrastructure (Cognito, Lambda, Search, Storage, DynamoDB, Network).
# Each stack's consumers are deployed after it, so they are deleted before it.
# Stacks that were never deployed are skipped. Deleting VoiceShop-DeciderEndpointStack
# deletes the SageMaker endpoint, which stops the GPU instance and its billing.
#
# After the stacks, it removes what no stack owns:
#   - log groups that services create on first use (CodeBuild, the SageMaker
#     endpoint, AgentCore runtimes, CDK custom-resource Lambdas), matched only by
#     VoiceShop names
#   - local deploy outputs (cdk-outputs/*.json, the frontend's .env.local and build)
#   - VoiceShop's own CDK bootstrap (VoiceShop-CDKToolkit) with its asset bucket and
#     ECR repository, so no uploaded asset is left
# The account's shared CDK bootstrap (CDKToolkit) is left alone: other projects use it,
# and VoiceShop no longer uploads to it. AWS itself keeps CloudWatch metric data (it
# expires after 15 months) and may keep account-wide service-linked roles.
#
# Only VoiceShop-named resources are touched; other projects in the account are not.
#
# Flags:
#   --dry-run              Print what would be deleted without deleting anything.
#   --force                Skip the interactive confirmation prompt.
#   --keep-decider-image   Keep VoiceShop-DeciderImageStack (the decider ECR repository
#                          and its 13.5 GB image, about $1.35 a month), so the next
#                          deploy-all.sh skips the ~16 minute image build.
#
# Credentials: deleting the SageMaker endpoint stack can take 15+ minutes. Each
# status check is a fresh CLI call, so a profile that refreshes its credentials
# (AWS IAM Identity Center, or credential_process) keeps working throughout.
set -euo pipefail

# Keep a Mac awake for the whole run: on idle sleep the script stops polling, and a
# deploy once sat frozen for 15 minutes after its stack had finished (2026-10-19).
# caffeinate exits when this script does.
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w $$ &
fi


# VOICESHOP_REGION, not AWS_REGION: a globally set AWS_REGION (for example one
# exported for another tool) must not silently redirect this cleanup elsewhere.
REGION="${VOICESHOP_REGION:-us-east-1}"
export AWS_REGION="${REGION}" AWS_DEFAULT_REGION="${REGION}" CDK_DEFAULT_REGION="${REGION}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=false
FORCE=false
KEEP_DECIDER_IMAGE=false
KEPT_STACK=VoiceShop-DeciderImageStack
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true ;;
    --keep-decider-image) KEEP_DECIDER_IMAGE=true ;;
    *) echo "Unknown flag: ${arg}" >&2; exit 1 ;;
  esac
done

# Deployment order, oldest first. Cleanup walks it backwards; stacks that are not
# deployed are skipped. Deleting VoiceShop-LambdaStack can take 10-30 minutes:
# AWS releases the network interfaces of the two VPC functions (search-catalog,
# catalog-index-writer) some time after the functions are deleted, and the
# security groups and subnet can only go once they are released (ADR-32).
DEPLOY_ORDER=(
  VoiceShop-NetworkStack
  VoiceShop-DynamoDBStack
  VoiceShop-StorageStack
  VoiceShop-SearchStack
  VoiceShop-LambdaStack
  VoiceShop-CognitoStack
  VoiceShop-CatalogDataStack
  VoiceShop-DeciderImageStack
  VoiceShop-DeciderInfraStack
  VoiceShop-DeciderEndpointStack
  VoiceShop-AgentCoreGatewayStack
  VoiceShop-AgentCoreInfraStack
  VoiceShop-AgentCoreRuntimeStack
  VoiceShop-FrontendStack
)

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

run() {
  if [[ "${DRY_RUN}" == true ]]; then
    echo "    [dry-run] $*"
  else
    "$@"
  fi
}

stack_status() {
  aws cloudformation describe-stacks --stack-name "$1" --region "${REGION}" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST"
}

# Polls with fresh CLI calls (not 'aws cloudformation wait', which keeps one set of
# credentials for up to an hour) until the stack is gone or its delete failed.
wait_for_delete() {
  local stack_name="$1" status deadline=$((SECONDS + 3600))
  while (( SECONDS < deadline )); do
    status="$(stack_status "${stack_name}")"
    case "${status}" in
      DOES_NOT_EXIST|DELETE_COMPLETE) return 0 ;;
      DELETE_FAILED) return 1 ;;
    esac
    sleep 20
  done
  return 1
}

delete_stack_if_present() {
  local stack_name="$1"
  local status
  status="$(stack_status "${stack_name}")"
  if [[ "${status}" == "DOES_NOT_EXIST" ]]; then
    echo "    ${stack_name}: not deployed, skipping."
    return
  fi
  echo "    deleting ${stack_name} (current status: ${status})..."
  run aws cloudformation delete-stack --stack-name "${stack_name}" --region "${REGION}"
  if [[ "${DRY_RUN}" == false ]]; then
    if ! wait_for_delete "${stack_name}"; then
      echo "❌ ${stack_name} did not delete (status: $(stack_status "${stack_name}")). Failed resources:" >&2
      aws cloudformation describe-stack-events --stack-name "${stack_name}" --region "${REGION}" \
        --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]" \
        --output text >&2 || true
      echo "   Fix the cause and re-run cleanup-all.sh; stacks already deleted are skipped." >&2
      exit 1
    fi
    echo "    ${stack_name} deleted."
  fi
}

# Deletes every object version and delete marker, then the bucket itself.
empty_and_delete_versioned_bucket() {
  local bucket="$1"
  if ! aws s3api head-bucket --bucket "${bucket}" >/dev/null 2>&1; then
    echo "    ${bucket}: not present, skipping."
    return
  fi
  echo "    emptying and deleting s3://${bucket} (all object versions)..."
  if [[ "${DRY_RUN}" == true ]]; then
    echo "    [dry-run] delete all object versions in ${bucket}, then delete-bucket"
    return
  fi
  local batch
  while true; do
    # Up to 1,000 versions/delete markers per pass, as a delete-objects payload.
    batch="$(aws s3api list-object-versions --bucket "${bucket}" --max-items 1000 --output json | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
page = json.loads(raw) if raw else {}
items = (page.get("Versions") or []) + (page.get("DeleteMarkers") or [])
print(json.dumps({"Objects": [{"Key": i["Key"], "VersionId": i["VersionId"]} for i in items], "Quiet": True}) if items else "")
')"
    [[ -n "${batch}" ]] || break
    aws s3api delete-objects --bucket "${bucket}" --delete "${batch}" >/dev/null
  done
  aws s3api delete-bucket --bucket "${bucket}"
  echo "    s3://${bucket} deleted."
}

# Log groups AWS services create on first use, so no stack owns them. Matched
# only by VoiceShop prefixes.
LOG_GROUP_PREFIXES=(
  /aws/codebuild/VoiceShop-
  /aws/sagemaker/Endpoints/voiceshop-
  /aws/lambda/VoiceShop-
  /aws/bedrock-agentcore/runtimes/voiceshop_
  /aws/vendedlogs/bedrock-agentcore/runtime/APPLICATION_LOGS/voiceshop_
  /aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/voiceshop
  /voiceshop/
)

delete_leftover_log_groups() {
  local prefix group found=false
  for prefix in "${LOG_GROUP_PREFIXES[@]}"; do
    for group in $(aws logs describe-log-groups --log-group-name-prefix "${prefix}" \
        --query 'logGroups[].logGroupName' --output text); do
      found=true
      echo "    deleting log group ${group}"
      run aws logs delete-log-group --log-group-name "${group}"
    done
  done
  [[ "${found}" == true ]] || echo "    no VoiceShop log groups left."
}

if [[ "${FORCE}" == false && "${DRY_RUN}" == false ]]; then
  echo "This destroys the entire VoiceShop solution in account ${ACCOUNT_ID}, ${REGION}:"
  echo "every VoiceShop stack (the SageMaker GPU endpoint, the AgentCore runtime, gateway and"
  echo "memory, the tool Lambdas, the OpenSearch collection, DynamoDB tables and their data,"
  echo "S3 buckets and their objects, ECR repositories, Cognito users, the Amplify app), the"
  echo "VoiceShop log groups, VoiceShop's own CDK bootstrap and its assets, and local deploy outputs. The catalog ships in the repository,"
  echo "so a later deploy-all.sh needs nothing restored."
  if [[ "${KEEP_DECIDER_IMAGE}" == true ]]; then
    echo "Kept: ${KEPT_STACK} (the decider ECR repository and image), for a faster redeploy."
  fi
  read -r -p "Continue? [y/N] " CONFIRM
  if [[ "${CONFIRM}" != "y" && "${CONFIRM}" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

echo "=== 1/4: VoiceShop CloudFormation stacks (reverse deployment order) ==="
for (( i=${#DEPLOY_ORDER[@]}-1; i>=0; i-- )); do
  if [[ "${KEEP_DECIDER_IMAGE}" == true && "${DEPLOY_ORDER[i]}" == "${KEPT_STACK}" ]]; then
    echo "    ${KEPT_STACK}: kept (--keep-decider-image)."
    continue
  fi
  delete_stack_if_present "${DEPLOY_ORDER[i]}"
done

echo "=== 2/4: VoiceShop CDK bootstrap and buckets no stack deletes ==="
# The bootstrap stack keeps its bucket and ECR repository on delete (CDK's default),
# so they are removed explicitly after the stack.
delete_stack_if_present VoiceShop-CDKToolkit
empty_and_delete_versioned_bucket "cdk-voiceshop-assets-${ACCOUNT_ID}-${REGION}"
CDK_IMAGES_REPO="cdk-voiceshop-container-assets-${ACCOUNT_ID}-${REGION}"
if aws ecr describe-repositories --repository-names "${CDK_IMAGES_REPO}" >/dev/null 2>&1; then
  echo "    deleting ECR repository ${CDK_IMAGES_REPO}"
  run aws ecr delete-repository --repository-name "${CDK_IMAGES_REPO}" --force >/dev/null
else
  echo "    ${CDK_IMAGES_REPO}: not present, skipping."
fi

echo "=== 3/4: Log groups created outside the stacks ==="
delete_leftover_log_groups

echo "=== 4/4: Local deploy outputs ==="
for path in "${REPO_ROOT}"/backend/*/cdk-outputs "${REPO_ROOT}"/backend/*/cdk/cdk-outputs "${REPO_ROOT}/frontend/cdk/cdk-outputs" \
    "${REPO_ROOT}/frontend/.env.local" "${REPO_ROOT}/frontend/dist" "${REPO_ROOT}/frontend/dist.zip" \
    "${REPO_ROOT}/deploy-decider.log"; do
  if [[ -e "${path}" ]]; then
    echo "    removing ${path#"${REPO_ROOT}/"}"
    run rm -rf "${path}"
  fi
done

if [[ "${DRY_RUN}" == true ]]; then
  echo ""
  echo "Dry run complete. Nothing was deleted."
  exit 0
fi

echo ""
echo "=== Verifying nothing VoiceShop remains ==="
FAILED=false
REMAINING_STACKS="$(aws cloudformation list-stacks --region "${REGION}" \
  --query "StackSummaries[?starts_with(StackName,'VoiceShop') && StackStatus!='DELETE_COMPLETE'].StackName" --output text)"
if [[ "${KEEP_DECIDER_IMAGE}" == true ]]; then
  REMAINING_STACKS="$(echo "${REMAINING_STACKS}" | tr '\t' '\n' | grep -vx "${KEPT_STACK}" | tr '\n' ' ' || true)"
  REMAINING_STACKS="${REMAINING_STACKS%% }"
fi
if [[ -n "${REMAINING_STACKS}" ]]; then
  echo "⚠️  Stacks still present: ${REMAINING_STACKS}" >&2
  FAILED=true
fi
REMAINING_ENDPOINTS="$(aws sagemaker list-endpoints --name-contains voiceshop --query "Endpoints[].EndpointName" --output text 2>/dev/null || true)"
if [[ -n "${REMAINING_ENDPOINTS}" ]]; then
  echo "⚠️  SageMaker endpoint(s) still present (still billing): ${REMAINING_ENDPOINTS}" >&2
  FAILED=true
fi
REMAINING_RUNTIMES="$(aws bedrock-agentcore-control list-agent-runtimes --region "${REGION}" \
  --query "agentRuntimes[?starts_with(agentRuntimeName,'voiceshop')].agentRuntimeName" --output text 2>/dev/null || true)"
if [[ -n "${REMAINING_RUNTIMES}" ]]; then
  echo "⚠️  AgentCore runtime(s) still present: ${REMAINING_RUNTIMES}" >&2
  FAILED=true
fi
REMAINING_BUCKETS="$(aws s3api list-buckets --query "Buckets[?starts_with(Name,'voiceshop') || starts_with(Name,'cdk-voiceshop-')].Name" --output text)"
if [[ -n "${REMAINING_BUCKETS}" ]]; then
  echo "⚠️  S3 bucket(s) still present: ${REMAINING_BUCKETS}" >&2
  FAILED=true
fi
REMAINING_COLLECTIONS="$(aws opensearchserverless list-collections \
  --query "collectionSummaries[?starts_with(name,'voiceshop')].name" --output text 2>/dev/null || true)"
if [[ -n "${REMAINING_COLLECTIONS}" ]]; then
  echo "⚠️  OpenSearch Serverless collection(s) still present: ${REMAINING_COLLECTIONS}" >&2
  FAILED=true
fi
REMAINING_AOSS_ENDPOINTS="$(aws opensearchserverless list-vpc-endpoints \
  --query "vpcEndpointSummaries[?starts_with(name,'voiceshop')].name" --output text 2>/dev/null || true)"
if [[ -n "${REMAINING_AOSS_ENDPOINTS}" ]]; then
  echo "⚠️  OpenSearch Serverless VPC endpoint(s) still present: ${REMAINING_AOSS_ENDPOINTS}" >&2
  FAILED=true
fi
REMAINING_VPCS="$(aws ec2 describe-vpcs --region "${REGION}" --filters Name=tag:Project,Values=VoiceShop \
  --query 'Vpcs[].VpcId' --output text 2>/dev/null || true)"
if [[ -n "${REMAINING_VPCS}" ]]; then
  echo "⚠️  VoiceShop VPC(s) still present: ${REMAINING_VPCS}" >&2
  FAILED=true
fi

if [[ "${FAILED}" == true ]]; then
  exit 1
fi
echo "✅ Cleanup complete. No VoiceShop stacks, endpoints, runtimes, buckets, collections or VPCs remain."
if [[ "${KEEP_DECIDER_IMAGE}" == true ]]; then
  echo "   Kept on purpose: ${KEPT_STACK} (decider image). Run cleanup-all.sh without the flag to remove it."
fi
