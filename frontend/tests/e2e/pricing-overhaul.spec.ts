import { expect, test } from '@playwright/test';

test.describe('Pricing Overhaul', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('access_token', 'test-access-token');
      localStorage.setItem('refresh_token', 'test-refresh-token');
    });

    await page.route('**/api/auth/me', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          user: {
            id: 'user-1',
            email: 'qa@example.com',
            full_name: 'QA User',
            plan: 'free',
          },
        }),
      });
    });
  });

  test('project checkout flow from pricing page', async ({ page }) => {
    await page.route('**/api/pricing/quote', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          quote: {
            id: 'quote-1',
            source_session_id: '11111111-1111-1111-1111-111111111111',
            user_id: 'user-1',
            old_url_count: 5200,
            new_url_count: 5000,
            billable_pages: 5200,
            pricing_version: 'v1_2026_03',
            currency: 'usd',
            line_items: [],
            subtotal_cents: 23400,
            status: 'draft',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          },
        }),
      });
    });

    await page.route('**/api/pricing/estimate**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          pricing_version: 'v1_2026_03',
          currency: 'usd',
          contact_required: false,
          billable_pages: 5000,
          line_items: [],
          subtotal_usd: '230.00',
          subtotal_cents: 23000,
          effective_rate_usd: '0.046',
        }),
      });
    });

    await page.route('**/api/billing/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          plan: 'free',
          pricing_version: 'v1_2026_03',
          manage_portal_available: false,
          agency: {
            has_subscription: false,
            subscription_id: null,
            status: null,
            current_period_start: null,
            current_period_end: null,
            cancel_at_period_end: false,
            usage_pages: 0,
            overage_enabled: false,
          },
        }),
      });
    });

    await page.route('**/api/billing/project/checkout', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          quote_id: 'quote-1',
          checkout_session_id: 'cs_test_project_1',
          url: 'http://127.0.0.1:3000/checkout-mock/project-session',
        }),
      });
    });

    await page.goto('/pricing?source_session_id=11111111-1111-1111-1111-111111111111');
    await expect(page.getByText('Unlock Deep Match For This Project')).toBeVisible();

    const checkoutUrlPromise = page.waitForURL('**/checkout-mock/project-session');
    await page.getByRole('button', { name: 'Unlock Deep Match' }).click();
    await checkoutUrlPromise;
  });

  test('agency checkout flow from pricing page', async ({ page }) => {
    await page.route('**/api/pricing/estimate**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          pricing_version: 'v1_2026_03',
          currency: 'usd',
          contact_required: false,
          billable_pages: 5000,
          line_items: [],
          subtotal_usd: '230.00',
          subtotal_cents: 23000,
          effective_rate_usd: '0.046',
        }),
      });
    });

    await page.route('**/api/billing/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          plan: 'free',
          pricing_version: 'v1_2026_03',
          manage_portal_available: false,
          agency: {
            has_subscription: false,
            subscription_id: null,
            status: null,
            current_period_start: null,
            current_period_end: null,
            cancel_at_period_end: false,
            usage_pages: 0,
            overage_enabled: false,
          },
        }),
      });
    });

    await page.route('**/api/billing/agency/checkout', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          checkout_session_id: 'cs_test_agency_1',
          url: 'http://127.0.0.1:3000/checkout-mock/agency-session',
        }),
      });
    });

    await page.goto('/pricing');
    await expect(page.getByText('Agency Plan')).toBeVisible();

    const checkoutUrlPromise = page.waitForURL('**/checkout-mock/agency-session');
    await page.getByRole('button', { name: 'Start Agency Checkout' }).click();
    await checkoutUrlPromise;
  });
});
