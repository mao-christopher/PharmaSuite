import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { newId } from './uuid.js';

test('HTTP fallback produces distinct RFC 4122 v4 IDs using secure random bytes', () => {
  const api = { getRandomValues: a => webcrypto.getRandomValues(a) };
  const values = Array.from({ length: 100 }, () => newId(api));
  assert.equal(new Set(values).size, 100);
  values.forEach(v => assert.match(v, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/));
});
