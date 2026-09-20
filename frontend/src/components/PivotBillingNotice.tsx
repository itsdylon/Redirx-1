import contract from '../../../contracts/pivot-v1.json';

// The same pinned contract is enforced by the test-only checkout services.
// Do not infer billing activation from a URL parameter or browser return.
export const PIVOT_TEST_BILLING = contract.policy.activation === 'test_only';

export function PivotBillingNotice() {
  return PIVOT_TEST_BILLING ? <p className="text-sm font-medium">
    Test checkout only. No real payment or live subscription is taken. Use Stripe test payment details.
  </p> : null;
}
