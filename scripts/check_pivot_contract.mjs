// Dependency-free JS consumer of the same fixtures used by Python policy tests.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const contract = JSON.parse(readFileSync(new URL('../contracts/pivot-v1.json', import.meta.url), 'utf8'));
const policy = contract.policy;
assert.equal(contract.contract_version, '1.0.0');
assert.equal(contract.status, 'implementation_target');
assert.equal(policy.activation, 'test_only');
assert.equal(policy.custom_quote_above, policy.bands.at(-1).max_pages);
assert.equal(new Set(contract.operation_statuses).size, contract.operation_statuses.length);
assert.ok(contract.terminal_operation_statuses.every(s => contract.operation_statuses.includes(s)));

let previous = 0;
for (const band of policy.bands) {
  assert.ok(Number.isSafeInteger(band.max_pages) && band.max_pages > previous);
  assert.ok(Number.isSafeInteger(band.amount_cents) && band.amount_cents >= 0);
  previous = band.max_pages;
}
for (const fixture of contract.pricing_fixtures) {
  const band = policy.bands.find(b => fixture.old_pages <= b.max_pages);
  assert.equal(band?.amount_cents ?? null, fixture.amount_cents, `${fixture.old_pages} pages`);
  assert.equal(band ? (band.amount_cents === 0 ? 'free' : 'fixed') : 'custom', fixture.kind);
}
for (const [name, tool] of Object.entries(contract.tools)) {
  assert.match(name, /^[a-z][a-z_]+$/);
  assert.ok(['GET', 'POST', 'PATCH'].includes(tool.method));
  assert.equal(tool.read_only, tool.method === 'GET');
  assert.equal(new Set(tool.required).size, tool.required.length);
  for (const [, parameter] of tool.path.matchAll(/\{([^}]+)\}/g)) {
    assert.ok(tool.required.includes(parameter), `${name} missing path argument ${parameter}`);
  }
}
assert.deepEqual(Object.keys(contract.tools).sort(), [
  'export_redirects', 'get_migration', 'import_inventory', 'list_matches', 'plan_migration',
  'refine_matches', 'resolve_matches', 'run_migration', 'verify_redirects',
]);
assert.equal(policy.automatic_monitoring_renewal, false);
assert.equal(policy.studio.automatic_overages, false);
console.log(`Pivot contract valid: ${Object.keys(contract.tools).length} tools, ${contract.pricing_fixtures.length} shared pricing cases; activation=${policy.activation}.`);
