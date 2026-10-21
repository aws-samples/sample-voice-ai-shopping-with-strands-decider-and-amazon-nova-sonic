import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';

const T = agentcore.SchemaDefinitionType;

// Keep in sync with backend/backend-infrastructure/lambda/shared/validation.js and
// the five handlers' parse() functions. The Lambdas re-validate every field; these
// schemas describe the tools to MCP clients. Department and category names are the
// catalog's (backend/catalog-data/generate/taxonomy.json).
const ASIN = { type: T.STRING, description: 'Product id (parent ASIN): 10 characters, A-Z and 0-9.' };
const SESSION_ID = { type: T.STRING, description: 'Shopping session id: 36-100 characters, letters, digits and hyphens.' };
const IDEMPOTENCY_KEY = { type: T.STRING, description: 'Caller-chosen key, at most 140 characters; a retry with the same key has no further effect.' };

export interface Tool {
  readonly targetName: string;
  readonly definition: agentcore.ToolDefinition;
}

export const TOOLS: Tool[] = [
  {
    targetName: 'search-catalog',
    definition: {
      name: 'search_catalog',
      description: 'Semantic product search over the curated catalog with hard filters for price, category, gender and availability. Returns ranked hits with thumbnail URLs.',
      inputSchema: {
        type: T.OBJECT,
        properties: {
          query: { type: T.STRING, description: 'What the shopper asked for, 1-500 characters.' },
          maxPriceCents: { type: T.INTEGER, description: 'Inclusive upper price bound in US cents (0-10,000,000).' },
          minPriceCents: { type: T.INTEGER, description: 'Inclusive lower price bound in US cents.' },
          departments: { type: T.ARRAY, description: 'Allowed departments (max 10), as named in the catalog, e.g. "Toys & Games".', items: { type: T.STRING } },
          categories: { type: T.ARRAY, description: 'Allowed categories (max 50), as named in the catalog, e.g. "Puzzles & Cubes".', items: { type: T.STRING } },
          genders: { type: T.ARRAY, description: 'Allowed genders (max 5), each one of: women, men, unisex, girls, boys.', items: { type: T.STRING } },
          availableOnly: { type: T.BOOLEAN, description: 'Only available products (default true).' },
          limit: { type: T.INTEGER, description: 'Maximum hits, 1-200 (default 200).' },
        },
        required: ['query'],
      },
    },
  },
  {
    targetName: 'get-product-detail',
    definition: {
      name: 'get_product_detail',
      description: 'Full record for one product: title, brand, price, rating, color, material, size, features, description and image URLs.',
      inputSchema: { type: T.OBJECT, properties: { asin: ASIN }, required: ['asin'] },
    },
  },
  {
    targetName: 'add-to-cart',
    definition: {
      name: 'add_to_cart',
      description: 'Add a product to the session cart (idempotent per idempotencyKey). Returns the updated cart with integer totals.',
      inputSchema: {
        type: T.OBJECT,
        properties: {
          sessionId: SESSION_ID,
          asin: ASIN,
          quantity: { type: T.INTEGER, description: 'Quantity to add, 1-10 (default 1).' },
          idempotencyKey: IDEMPOTENCY_KEY,
        },
        required: ['sessionId', 'asin', 'idempotencyKey'],
      },
    },
  },
  {
    targetName: 'remove-from-cart',
    definition: {
      name: 'remove_from_cart',
      description: 'Remove one product line from the session cart, or lower its quantity to keepQuantity. Returns the updated cart with integer totals.',
      inputSchema: {
        type: T.OBJECT,
        properties: {
          sessionId: SESSION_ID,
          asin: ASIN,
          keepQuantity: { type: T.INTEGER, description: 'How many of the product stay in the cart, 1-9; absent or 0 removes the line.' },
        },
        required: ['sessionId', 'asin'],
      },
    },
  },
  {
    targetName: 'place-order',
    definition: {
      name: 'place_order',
      description: 'Place the session cart as one order and clear the cart. No payment is taken. Returns the order, or EmptyCart.',
      inputSchema: {
        type: T.OBJECT,
        properties: { sessionId: SESSION_ID, idempotencyKey: IDEMPOTENCY_KEY },
        required: ['sessionId', 'idempotencyKey'],
      },
    },
  },
];
