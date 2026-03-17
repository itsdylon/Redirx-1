import { expect, test } from '@playwright/test';

test.describe('Landing Attribution Handoff', () => {
  test('persists landing attribution through quick-match to login transition', async ({ page }) => {
    await page.goto('/quick-match?source=landing&surface=hero_primary_cta&campaign=spring_launch');

    await expect(page.getByRole('heading', { name: 'Free Redirect Map Generator' })).toBeVisible();

    const beforeLogin = await page.evaluate(() => localStorage.getItem('redirx_landing_attribution_v1'));
    expect(beforeLogin).toBeTruthy();
    expect(beforeLogin).toContain('"source":"landing"');
    expect(beforeLogin).toContain('"surface":"hero_primary_cta"');
    expect(beforeLogin).toContain('"campaign":"spring_launch"');

    await page.getByRole('banner').getByRole('button', { name: 'Log in' }).click();
    await expect(page).toHaveURL(/\/login\?redirect=%2Fquick-match&source=quick-match/);

    const afterLogin = await page.evaluate(() => localStorage.getItem('redirx_landing_attribution_v1'));
    expect(afterLogin).toBe(beforeLogin);
  });
});
