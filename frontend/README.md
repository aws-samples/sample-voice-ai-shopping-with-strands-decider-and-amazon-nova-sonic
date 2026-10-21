# VoiceShop frontend

React + Vite + TypeScript shopping UI, hosted on AWS Amplify Hosting (`VoiceShop-FrontendStack`, in `cdk/`). It runs only against the deployed backend: there is no mock mode.

- **Sign-in:** Amazon Cognito User Pool. Temporary AWS credentials come from the Cognito Identity Pool.
- **Session:** one SigV4-presigned WebSocket to the session host on AgentCore Runtime (`src/services/AgentCoreTransport.ts`). The first message is always `auth` with the access token. The message types are in `src/services/protocol.ts`.
- **Audio:** the mic is captured at 16 kHz PCM16 through an `AudioWorklet`; replies play at 24 kHz.

## What the shopper sees

- **Shelf:** up to 12 product cards, each with its on-screen number. Say "number 5" or describe it ("the red one"), or tap a card.
- **Chips:** committed choices (recipient, gender, department, category, budget) with their confidence. Click any chip (recipient, gender, department, category or budget) to remove it.
- **Voice on / Voice off:** spoken replies are off by default and the choice is remembered per browser. Yes/no confirmations ("Do you mean No. 5, ...?") are spoken either way.
- **Voice off:** the chat shows the app's reply text.
- **More and moving:** "show me more" adds the next 12 results, up to 49. "Scroll down" and "back to the top" move the page; "close it" closes the open product.
- **Yes/No buttons:** every yes/no question shows them. If the browser already allows the mic, it opens automatically for the answer.
- **Cart and order:** the cart updates live. Remove a line by voice ("remove the blouse from my cart") or with its ×; "empty my cart" asks first. "Place my order" always asks "Should I place the order?" first, and an empty cart is reported. The order is a DynamoDB record. No payment is taken.
- **Behind the scenes:** the debug panel. Press **D** or use the footer button **Debug overlay** for each sentence's questions, top answers, candidate counts and timings. **Pop out** opens it in a separate window.

## Deploy

`deploy-all.sh` step 7 deploys the Amplify app and publishes the build. To do it by hand after the backend is deployed:

```bash
npm ci
cd cdk && npm ci && npx cdk deploy VoiceShop-FrontendStack --outputs-file cdk-outputs/frontend.json
cd .. && npm run deploy:amplify   # writes .env.local from the CDK outputs, builds, uploads to Amplify
```

## Run locally against the deployed backend

```bash
npm ci
npm run dev   # writes .env.local from the CDK outputs, then serves http://127.0.0.1:3000
```

`scripts/generate-config.js` reads the Cognito and session host outputs that `deploy-all.sh` saves. If they are missing, it says which stack to deploy. `.env.local` is gitignored.

Sign in as `AppUser` (or the email you passed to `--user-email`). The first sign-in uses the temporary password from the Cognito invitation email.

Try: "a gift for my mom under eighty dollars", "show me watches", "number 3", "add it to my cart", "something for my parent", "show me more", "remove the blouse from my cart", "go back", "start over", "place my order".
