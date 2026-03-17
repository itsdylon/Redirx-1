import { beforeEach, describe, expect, it } from "vitest";

import {
  buildConversionEventProps,
  clearLandingAttributionForTests,
  getLandingAttribution,
  persistLandingAttributionFromUrl,
} from "./analyticsAttribution";

describe("analyticsAttribution", () => {
  beforeEach(() => {
    clearLandingAttributionForTests();
    window.history.replaceState({}, "", "/");
  });

  it("persists landing attribution from URL params", () => {
    window.history.replaceState(
      {},
      "",
      "/quick-match?source=landing&surface=hero_primary_cta&campaign=launch"
    );

    persistLandingAttributionFromUrl();
    const attribution = getLandingAttribution();

    expect(attribution).toEqual(
      expect.objectContaining({
        source: "landing",
        surface: "hero_primary_cta",
        campaign: "launch",
        sourceRepo: "landing",
      })
    );
  });

  it("ignores internal app source params", () => {
    window.history.replaceState({}, "", "/login?source=quick-match");

    persistLandingAttributionFromUrl();

    expect(getLandingAttribution()).toBeNull();
  });

  it("builds conversion event props from persisted attribution", () => {
    window.history.replaceState({}, "", "/quick-match?source=landing&surface=navbar_get_started");
    persistLandingAttributionFromUrl();

    const props = buildConversionEventProps({
      plan: "free",
      authenticated: false,
    });

    expect(props).toEqual(
      expect.objectContaining({
        source_repo: "landing",
        landing_source: "landing",
        entry_surface: "navbar_get_started",
        plan: "free",
        authenticated: false,
      })
    );
  });
});
