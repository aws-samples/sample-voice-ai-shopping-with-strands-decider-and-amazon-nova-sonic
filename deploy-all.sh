#!/usr/bin/env bash
# Deploys VoiceShop. Two chains run side by side:
#   decider (background, deploy-decider.log): D1 ECR repository and decider infra,
#     D2 the model upload (in CodeBuild), D3 the image build, D4 the SageMaker endpoint
#   first: 0 a CDK bootstrap for VoiceShop only (VoiceShop-CDKToolkit, qualifier voiceshop)
#   main: 1 backend-infrastructure (Network, DynamoDB, Storage, Search, tool Lambdas, Cognito),
#     2 the synthetic catalog, 3 the AgentCore Gateway, 4-6 the session host on
#     AgentCore Runtime (infra, image build, runtime), 7 the frontend on Amplify
# The script waits for both, then pre-warms the decider endpoint.
# Re-running is safe: stacks update in place and images already built are skipped.
#
# Flags:
#   --user-email <e>  Email of the demo Cognito user (sign-in username AppUser);
#                     required on the first deploy of VoiceShop-CognitoStack, later
#                     deploys keep the old value.
#   --user-name <n>   Display name of that user (default AppUser).
#   --skip-catalog    Skip loading the catalog (step 2).
#   --decider-instance-type <t>
#                     SageMaker instance type for the decider, without "ml."
#                     (default g6.xlarge; g5.xlarge and g6e.xlarge also work).
#   --decider-max-instances <n>
#                     Upper bound for decider endpoint auto-scaling (default 1).
#                     Must not exceed your account's SageMaker quota
#                     "ml.<type> for endpoint usage" (Service Quotas); request an
#                     increase there first if needed.
#
# Credentials: a fresh run takes about 31 minutes (measured 2026-10-21; a little faster when the
# decider image was kept by cleanup-all.sh --keep-decider-image), and some steps wait 15+ minutes. With
# short-lived credentials, use a profile that refreshes itself (AWS IAM Identity
# Center, or credential_process), e.g. AWS_PROFILE=<profile> ./deploy-all.sh ...
set -euo pipefail

# Keep a Mac awake for the whole run: on idle sleep the script stops polling, and a
# deploy once sat frozen for 15 minutes after its stack had finished (2026-10-19).
# caffeinate exits when this script does.
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w $$ &
fi


SKIP_CATALOG=false
USER_EMAIL=""
USER_NAME=""
DECIDER_MAX_INSTANCES=1
DECIDER_INSTANCE_TYPE=g6.xlarge
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-catalog) SKIP_CATALOG=true; shift ;;
    --user-email) USER_EMAIL="${2:?--user-email needs a value}"; shift 2 ;;
    --user-name) USER_NAME="${2:?--user-name needs a value}"; shift 2 ;;
    --decider-max-instances) DECIDER_MAX_INSTANCES="${2:?--decider-max-instances needs a value}"; shift 2 ;;
    --decider-instance-type) DECIDER_INSTANCE_TYPE="${2:?--decider-instance-type needs a value}"; DECIDER_INSTANCE_TYPE="${DECIDER_INSTANCE_TYPE#ml.}"; shift 2 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

# VOICESHOP_REGION, not AWS_REGION: a globally set AWS_REGION (for example one
# exported for another tool) must not silently redirect this deploy elsewhere.
REGION="${VOICESHOP_REGION:-us-east-1}"
export AWS_REGION="${REGION}" AWS_DEFAULT_REGION="${REGION}" CDK_DEFAULT_REGION="${REGION}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_INFRA_DIR="${REPO_ROOT}/backend/backend-infrastructure"
DECIDER_CDK_DIR="${REPO_ROOT}/backend/decider-runtime/cdk"
CATALOG_CDK_DIR="${REPO_ROOT}/backend/catalog-data/cdk"
AGENTCORE_CDK_DIR="${REPO_ROOT}/backend/agentcore-runtime/cdk"


# Returns 0 (true) if the named CloudFormation stack exists in a completed state
# (used to tell a first Cognito deploy, which needs --user-email, from a later one).
stack_is_up_to_date() {
  local stack_name="$1"
  local status
  status="$(aws cloudformation describe-stacks --stack-name "${stack_name}" --region "${REGION}" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "DOES_NOT_EXIST")"
  [[ "${status}" == "CREATE_COMPLETE" || "${status}" == "UPDATE_COMPLETE" ]]
}

# Checked before anything is deployed, so a bad value fails in seconds, not after the builds.
if ! [[ "${DECIDER_MAX_INSTANCES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "❌ --decider-max-instances must be a positive integer, got '${DECIDER_MAX_INSTANCES}'." >&2
  exit 1
fi
# The quota caps how far auto-scaling can actually go: fail now rather than at scale-out time.
QUOTA_NAME="ml.${DECIDER_INSTANCE_TYPE} for endpoint usage"
# Quota codes of the instance types this sample supports (one fast lookup); any other
# type is found by name in the full listing, which takes about 2 minutes.
case "${DECIDER_INSTANCE_TYPE}" in
  g5.xlarge) QUOTA_CODE=L-1928E07B ;;
  g6.xlarge) QUOTA_CODE=L-D470D954 ;;
  g6e.xlarge) QUOTA_CODE=L-B0729CB4 ;;
  *) QUOTA_CODE="" ;;
esac
if [[ -n "${QUOTA_CODE}" ]]; then
  DECIDER_QUOTA="$(aws service-quotas get-service-quota --service-code sagemaker --quota-code "${QUOTA_CODE}" \
    --query 'Quota.Value' --output text 2>/dev/null || true)"
else
  DECIDER_QUOTA="$(aws service-quotas list-service-quotas --service-code sagemaker --query 'Quotas[].[QuotaName,Value]' \
    --output text 2>/dev/null | awk -F'\t' -v name="${QUOTA_NAME}" '$1 == name { print $2 }' || true)"
fi
[[ -n "${DECIDER_QUOTA}" ]] || DECIDER_QUOTA=unknown
if [[ "${DECIDER_QUOTA}" == "0.0" ]]; then
  echo "❌ Your account has no SageMaker quota for \"${QUOTA_NAME}\". Request one in Service Quotas, or pick another --decider-instance-type." >&2
  exit 1
fi
if [[ "${DECIDER_QUOTA}" != "unknown" ]] && (( DECIDER_MAX_INSTANCES > ${DECIDER_QUOTA%.*} )); then
  echo "❌ --decider-max-instances ${DECIDER_MAX_INSTANCES} exceeds your quota of ${DECIDER_QUOTA%.*} for" >&2
  echo "   \"${QUOTA_NAME}\". Request an increase in Service Quotas (SageMaker) first." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "=== 0: VoiceShop-CDKToolkit (a CDK bootstrap for this project only, so cleanup can remove every asset) ==="
( cd "${BACKEND_INFRA_DIR}" && npm ci --silent && npx cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}" \
    --qualifier voiceshop --toolkit-stack-name VoiceShop-CDKToolkit --tags Project=VoiceShop >/dev/null \
  && echo "    ready." )

# The decider chain (its stacks, the model upload, the image build, the endpoint)
# depends on nothing else, and nothing else waits for it until the end: the session
# host knows the endpoint by its fixed name. So it runs in the background, logged
# to deploy-decider.log, while the main chain below deploys.
decider_chain() {
  echo "=== D1: VoiceShop-DeciderImageStack (ECR repository; kept by cleanup-all.sh --keep-decider-image) ==="
  ( cd "${DECIDER_CDK_DIR}" && npm ci --silent && npx cdk deploy VoiceShop-DeciderImageStack VoiceShop-DeciderInfraStack \
      --require-approval never --context deciderMaxInstances="${DECIDER_MAX_INSTANCES}" --context deciderInstanceType="${DECIDER_INSTANCE_TYPE}" )
  echo "=== D2: Model upload, in CodeBuild (Hugging Face -> s3://voiceshop-decider-model-${ACCOUNT_ID}-${REGION}/model/; skipped when present) ==="
  "${REPO_ROOT}/codebuild-scripts/build-image.sh" VoiceShop-DeciderModelUpload 20
  echo "=== D3: Decider image (CodeBuild; skipped when this container version is already in ECR) ==="
  "${REPO_ROOT}/codebuild-scripts/build-image.sh" VoiceShop-DeciderBuild 30
  echo "=== D4: VoiceShop-DeciderEndpointStack (SageMaker endpoint on ml.${DECIDER_INSTANCE_TYPE}, auto-scaling 1-${DECIDER_MAX_INSTANCES}; creation takes ~15 minutes) ==="
  ( cd "${DECIDER_CDK_DIR}" && npx cdk deploy VoiceShop-DeciderEndpointStack --require-approval never \
      --context deciderMaxInstances="${DECIDER_MAX_INSTANCES}" --context deciderInstanceType="${DECIDER_INSTANCE_TYPE}" \
      --outputs-file cdk-outputs/decider.json )
}
DECIDER_LOG="${REPO_ROOT}/deploy-decider.log"
decider_chain > "${DECIDER_LOG}" 2>&1 &
DECIDER_PID=$!
echo "=== Decider chain started in the background (pid ${DECIDER_PID}); progress: tail -f deploy-decider.log ==="
# If the main chain fails, say so: the decider chain keeps going on its own.
trap 'status=$?; if (( status != 0 )) && kill -0 "${DECIDER_PID}" 2>/dev/null; then
  echo "ℹ️  The decider chain is still running in the background (deploy-decider.log); re-run this script when it is done." >&2
fi' EXIT

echo "=== 1/7: VoiceShop-NetworkStack, -DynamoDBStack, -StorageStack, -SearchStack, -LambdaStack, -CognitoStack ==="
COGNITO_PARAMETERS=()
if [[ -n "${USER_EMAIL}" ]]; then COGNITO_PARAMETERS+=(--parameters "VoiceShop-CognitoStack:UserEmail=${USER_EMAIL}"); fi
if [[ -n "${USER_NAME}" ]]; then COGNITO_PARAMETERS+=(--parameters "VoiceShop-CognitoStack:UserName=${USER_NAME}"); fi
if ! stack_is_up_to_date VoiceShop-CognitoStack && [[ -z "${USER_EMAIL}" ]]; then
  echo "❌ First deploy of VoiceShop-CognitoStack: pass --user-email <email>." >&2
  exit 1
fi
( cd "${BACKEND_INFRA_DIR}" && npm ci --silent && npx cdk deploy VoiceShop-NetworkStack VoiceShop-DynamoDBStack VoiceShop-StorageStack VoiceShop-SearchStack \
    VoiceShop-LambdaStack VoiceShop-CognitoStack --require-approval never ${COGNITO_PARAMETERS[@]+"${COGNITO_PARAMETERS[@]}"} \
    --outputs-file cdk-outputs/backend-infrastructure.json )

if [[ "${SKIP_CATALOG}" == true ]]; then
  echo "=== 2/7: catalog skipped (--skip-catalog) ==="
else
  echo "=== 2/7: Synthetic catalog (VoiceShop-CatalogDataStack, then the VoiceShop-CatalogCuration job) ==="
  ( cd "${CATALOG_CDK_DIR}" && npm ci --silent && npx cdk deploy VoiceShop-CatalogDataStack --require-approval never \
      --outputs-file cdk-outputs/catalog-data.json )
  "${REPO_ROOT}/codebuild-scripts/build-image.sh" VoiceShop-CatalogCuration 20
fi


echo "=== 3/7: VoiceShop-AgentCoreGatewayStack (MCP gateway, IAM auth, five Lambda targets) ==="
( cd "${REPO_ROOT}/backend/agentcore-gateway/cdk" && npm ci --silent && npx cdk deploy VoiceShop-AgentCoreGatewayStack \
    --require-approval never --outputs-file cdk-outputs/agentcore-gateway.json )

echo "=== 4/7: VoiceShop-AgentCoreInfraStack (session host ECR repo, ARM64 CodeBuild, AgentCore Memory, runtime role) ==="
( cd "${AGENTCORE_CDK_DIR}" && npm ci --silent && npx cdk deploy VoiceShop-AgentCoreInfraStack \
    --require-approval never --outputs-file cdk-outputs/agentcore-infra.json )

echo "=== 5/7: Build and push the session host image (skipped when this version is already in ECR) ==="
"${REPO_ROOT}/codebuild-scripts/build-image.sh" VoiceShop-SessionHostBuild 30

echo "=== 6/7: VoiceShop-AgentCoreRuntimeStack (session host WebSocket runtime) ==="
( cd "${AGENTCORE_CDK_DIR}" && npx cdk deploy VoiceShop-AgentCoreRuntimeStack \
    --require-approval never --outputs-file cdk-outputs/agentcore-runtime.json )

echo "=== 7/7: VoiceShop-FrontendStack (Amplify app) and the frontend build ==="
( cd "${REPO_ROOT}/frontend" && npm ci --silent \
  && ( cd cdk && npm ci --silent && npx cdk deploy VoiceShop-FrontendStack --require-approval never \
         --outputs-file cdk-outputs/frontend.json ) \
  && npm run deploy:amplify )
FRONTEND_URL="$(python3 -c "import json; print(json.load(open('${REPO_ROOT}/frontend/cdk/cdk-outputs/frontend.json'))['VoiceShop-FrontendStack']['AmplifyAppUrl'])")"

echo "=== Waiting for the decider chain (see deploy-decider.log) ==="
if ! wait "${DECIDER_PID}"; then
  echo "❌ The decider chain failed. Last lines of deploy-decider.log:" >&2
  tail -n 40 "${DECIDER_LOG}" >&2
  exit 1
fi
grep -E '^=== D' "${DECIDER_LOG}" || true
PREWARM_VENV="$(mktemp -d)"
python3 -m venv "${PREWARM_VENV}" && "${PREWARM_VENV}/bin/pip" install --quiet boto3
"${PREWARM_VENV}/bin/python3" "${REPO_ROOT}/backend/decider-runtime/scripts/prewarm.py" --region "${REGION}"
rm -rf "${PREWARM_VENV}"


echo ""
echo "✅ Deploy complete. Open ${FRONTEND_URL} and sign in as AppUser (the temporary"
echo "   password is in the Cognito invitation email sent to --user-email)."
echo "   GPU cost reminder: the decider endpoint keeps at least one ml.${DECIDER_INSTANCE_TYPE} running"
echo "   (ml.g6.xlarge: about \$1.13/hour on-demand in us-east-1) for as long as it is deployed. Run"
echo "   cleanup-all.sh when you are done testing or recording."
