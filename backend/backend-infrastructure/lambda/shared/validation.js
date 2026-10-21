/**
 * Input validation shared by the five tool Lambdas. Every tool returns
 * { error: "ValidationError", message } for bad input (the Gateway tool schemas)
 * and rethrows anything unexpected, so the Gateway reports it as a tool error.
 */

// Department and category names come from the catalog (backend/catalog-data/generate/
// taxonomy.json); here they are only checked for shape. They reach OpenSearch as
// term filters, never as query text.
const LABEL_PATTERN = /^[A-Za-z0-9&' ,.-]{1,40}$/;
const GENDERS = ['women', 'men', 'unisex', 'girls', 'boys'];

const ASIN_PATTERN = /^[A-Z0-9]{10}$/;
const SESSION_ID_PATTERN = /^[A-Za-z0-9-]{36,100}$/;
const MAX_PRICE_CENTS = 10_000_000;

class ValidationError extends Error {}

function validationErrorResponse(err) {
  return { error: 'ValidationError', message: err.message };
}

/** Runs a tool: validation errors become a tool result, anything else is rethrown. */
async function runTool(name, event, parse, run) {
  let input;
  try {
    input = parse(event || {});
  } catch (err) {
    if (err instanceof ValidationError) {
      console.warn(`⚠️ ${name} validation error: ${err.message}`);
      return validationErrorResponse(err);
    }
    throw err;
  }
  try {
    return await run(input);
  } catch (err) {
    if (err instanceof ValidationError) return validationErrorResponse(err);
    console.error(`❌ ${name} failed`, err);
    throw err;
  }
}

function requireString(input, field, { min = 1, max, pattern } = {}) {
  const value = input[field];
  if (typeof value !== 'string') throw new ValidationError(`${field} must be a string`);
  if (value.length < min || (max !== undefined && value.length > max)) {
    throw new ValidationError(max === undefined ? `${field} must be at least ${min} characters` : `${field} must be ${min}-${max} characters`);
  }
  if (!value.trim()) throw new ValidationError(`${field} must not be blank`);
  if (pattern && !pattern.test(value)) throw new ValidationError(`${field} has an invalid format`);
  return value;
}

function optionalInt(input, field, { min, max, defaultValue } = {}) {
  const value = input[field];
  if (value === undefined || value === null) return defaultValue;
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new ValidationError(`${field} must be an integer from ${min} to ${max}`);
  }
  return value;
}

function optionalBool(input, field, defaultValue) {
  const value = input[field];
  if (value === undefined || value === null) return defaultValue;
  if (typeof value !== 'boolean') throw new ValidationError(`${field} must be a boolean`);
  return value;
}

function optionalEnumList(input, field, allowed, maxItems) {
  const value = input[field];
  if (value === undefined || value === null) return undefined;
  if (!Array.isArray(value) || value.length > maxItems) {
    throw new ValidationError(`${field} must be an array of at most ${maxItems} values`);
  }
  for (const item of value) {
    if (!allowed.includes(item)) throw new ValidationError(`${field} contains an unknown value: ${item}`);
  }
  return value.length ? [...new Set(value)] : undefined;
}

function optionalLabelList(input, field, maxItems) {
  const value = input[field];
  if (value === undefined || value === null) return undefined;
  if (!Array.isArray(value) || value.length > maxItems || !value.every((v) => typeof v === 'string' && LABEL_PATTERN.test(v))) {
    throw new ValidationError(`${field} must be an array of at most ${maxItems} names (letters, digits, spaces, & ' , . -)`);
  }
  return value.length ? [...new Set(value)] : undefined;
}

module.exports = {
  GENDERS,
  ASIN_PATTERN,
  SESSION_ID_PATTERN,
  MAX_PRICE_CENTS,
  ValidationError,
  runTool,
  requireString,
  optionalInt,
  optionalBool,
  optionalEnumList,
  optionalLabelList,
};
