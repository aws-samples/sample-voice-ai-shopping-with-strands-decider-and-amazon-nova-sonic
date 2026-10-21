# AgentCore Gateway

`VoiceShop-AgentCoreGatewayStack`: one MCP gateway (`voiceshop-gateway`) with `AWS_IAM` inbound auth and one Lambda target per tool. The five tools are `search_catalog`, `get_product_detail`, `add_to_cart`, `remove_from_cart` and `place_order` (the Lambdas live in `backend-infrastructure`). Only the session host calls it, with SigV4 (service `bedrock-agentcore`) from its runtime role. Gateway names each tool `<target>___<tool>`, so clients resolve tools by suffix.

**Construct choice.** The pinned `@aws-cdk/aws-bedrock-agentcore-alpha` 2.235.1-alpha.0 ships `Gateway`, `GatewayTarget`, `LambdaTargetConfiguration`, `ToolSchema.fromInline` and `GatewayAuthorizer.usingAwsIam()`. The stack uses those, not the template's custom-resource (`cr.Provider` + `CreateGateway`) pattern. The tool schemas in `cdk/lib/tool-schemas.ts` mirror the Lambdas' validators, and the Lambdas re-validate every field.

**Test client.** `test-client/gateway-check.py` lists the tools, calls each with valid and invalid input (10 checks), and measures the latency Gateway adds to `search_catalog`. That was 219 ms p50 from a laptop, including the internet round trip, on 2026-10-18.
