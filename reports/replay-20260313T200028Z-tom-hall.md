# User Replay + Funnel Diagnostics: tom.hall@sharpahead.com

- Generated at: `2026-03-13T20:00:28.439538+00:00`
- User ID: `aa9c9869-92eb-4aa1-80e0-4f149b3d780e`
- User plan (current): `free`
- Requested source sessions: `0edf6c7b-043d-4acd-8bdf-3986721b3642, 6cd2af17-a20f-49d9-b7f5-81eba870cdf7`

## Logs and Evidence
Runtime API/worker logs were not available in this replay. Canonical evidence uses DB rows from: `migration_sessions`, `url_mappings`, `deep_match_previews`, `project_pricing_quotes`, `user_profiles`.

## Chronological Event Timeline
| Timestamp (UTC) | Event | Session | Evidence | Notes |
|---|---|---|---|---|
| 2026-03-13 12:55:31 UTC | `session_created` | `0edf6c7b-043d-4acd-8bdf-3986721b3642` | `migration_sessions:0edf6c7b-043d-4acd-8bdf-3986721b3642` | Session created with pipeline=url_only status_now=completed old=87 new=159 |
| 2026-03-13 12:55:31 UTC | `mapping_first_seen` | `0edf6c7b-043d-4acd-8bdf-3986721b3642` | `url_mappings:session_id=0edf6c7b-043d-4acd-8bdf-3986721b3642` | First url_mappings row observed for this session (count_now=77). (inferred) |
| 2026-03-13 12:55:35 UTC | `mapping_last_seen` | `0edf6c7b-043d-4acd-8bdf-3986721b3642` | `url_mappings:session_id=0edf6c7b-043d-4acd-8bdf-3986721b3642` | Last url_mappings row observed for this session (count_now=77). (inferred) |
| 2026-03-13 12:56:06 UTC | `quote_created` | `0edf6c7b-043d-4acd-8bdf-3986721b3642` | `project_pricing_quotes:b9c33afe-4997-499a-b146-aff07217b88e` | Quote created with status=draft subtotal_cents=1590. |
| 2026-03-13 15:30:07 UTC | `session_created` | `6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | `migration_sessions:6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | Session created with pipeline=url_only status_now=completed old=1001 new=1237 |
| 2026-03-13 15:30:08 UTC | `mapping_first_seen` | `6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | `url_mappings:session_id=6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | First url_mappings row observed for this session (count_now=133). (inferred) |
| 2026-03-13 15:30:17 UTC | `mapping_last_seen` | `6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | `url_mappings:session_id=6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | Last url_mappings row observed for this session (count_now=133). (inferred) |
| 2026-03-13 15:31:07 UTC | `quote_created` | `6cd2af17-a20f-49d9-b7f5-81eba870cdf7` | `project_pricing_quotes:a25f9f86-1d74-42b3-9ac2-40faae6dfa9b` | Quote created with status=draft subtotal_cents=8685. |

## Per-Session Summary
### Session `0edf6c7b-043d-4acd-8bdf-3986721b3642`
- Status: `completed` | Pipeline: `url_only` | Preview session: `False`
- URL counts: old=`87`, new=`159`, billable=`159`
- Mappings: total=`77`, needs_review=`36`, confidence(min/avg/max)=`0.4333/0.7403/0.9`
- Mapping quality distribution: `{'high_ge_0.85': 38, 'medium_0.65_to_0.8499': 8, 'low_lt_0.65': 31}`
- Match type distribution: `{'slug_match': 37, 'url_high': 4, 'url_medium': 17, 'url_low': 19}`

### Session `6cd2af17-a20f-49d9-b7f5-81eba870cdf7`
- Status: `completed` | Pipeline: `url_only` | Preview session: `False`
- URL counts: old=`1001`, new=`1237`, billable=`1237`
- Mappings: total=`133`, needs_review=`128`, confidence(min/avg/max)=`0.4034/0.5196/1.0`
- Mapping quality distribution: `{'high_ge_0.85': 4, 'medium_0.65_to_0.8499': 3, 'low_lt_0.65': 126}`
- Match type distribution: `{'exact_url': 2, 'slug_match': 1, 'url_high': 2, 'url_medium': 21, 'url_low': 107}`

## Risky Mapping Samples User Likely Saw
### Session `0edf6c7b-043d-4acd-8bdf-3986721b3642`
1. risk=`1.0687` conf=`0.4333` needs_review=`True` `https://redgravesearch.com/publications/partnering-with-executive-search-firm/` -> `https://redgravesearch.com/executive-search/`
2. risk=`1.0394` conf=`0.4706` needs_review=`True` `https://redgravesearch.com/filter/` -> `https://redgravesearch.com/candidate/`
3. risk=`1.0381` conf=`0.4779` needs_review=`True` `https://redgravesearch.com/publications/exploring-board-roles-starting` -> `https://redgravesearch.com/insights/publications/the-human-skills-that-will-mat…`
4. risk=`1.0378` conf=`0.4772` needs_review=`True` `https://redgravesearch.com/publications/are-businesses-underestimating-the-valu…` -> `https://redgravesearch.com/insights/case-studies/energising-the-leadership-team…`
5. risk=`1.025` conf=`0.48` needs_review=`True` `https://redgravesearch.com/publications/exploring-` -> `https://redgravesearch.com/operations-transformation/`
6. risk=`1.005` conf=`0.5` needs_review=`True` `https://redgravesearch.com/wp-content/*` -> `https://redgravesearch.com/contact-us/`
7. risk=`0.9994` conf=`0.5096` needs_review=`True` `https://redgravesearch.com/publications/the-secrets-of-ceo-success-how-women-ar…` -> `https://redgravesearch.com/insights/publications/ceo-succession-trends-across-b…`
8. risk=`0.9955` conf=`0.5055` needs_review=`True` `https://redgravesearch.com/publications/impact-of-workplace-culture-on-performa…` -> `https://redgravesearch.com/workplace-culture-competitive-edge/`

### Session `6cd2af17-a20f-49d9-b7f5-81eba870cdf7`
1. risk=`1.1267` conf=`0.4553` needs_review=`True` `https://www.ebp.be/fr/services-intégrés-de-facility-management-pour-elia_elia-a…` -> `https://www.ebp.be/fr/event/congres-fonction-publique-2025/`
2. risk=`1.1076` conf=`0.4034` needs_review=`True` `https://www.ebp.be/nl/n124-hoogstraten-structureel-onderhoud_mobiliteit-en-open…` -> `https://www.ebp.be/fr/verklarende_woorden/awv-agentschap-wegen-en-verkeer/`
3. risk=`1.1005` conf=`0.4035` needs_review=`True` `https://www.ebp.be/nl/bda-overheidsopdracht-openbare-of-niet-openbare-procedure…` -> `https://www.ebp.be/nl/verklarende_woorden/niet-openbare-procedure/`
4. risk=`1.0825` conf=`0.4545` needs_review=`True` `https://www.ebp.be/fr/construction-dune-voirie-portuaire-et-amenagements-dans-l…` -> `https://www.ebp.be/fr/decouvrez-vos-opportunites-dans-les-marches-publics-et-le…`
5. risk=`1.079` conf=`0.407` needs_review=`True` `https://www.ebp.be/fr/uitnodiging-tot-indienen-offerte-inhuur-hoogwerkers-veree…` -> `https://www.ebp.be/fr/verklarende_woorden/vereenvoudigde-onderhandelingsprocedu…`
6. risk=`1.0755` conf=`0.4545` needs_review=`True` `https://www.ebp.be/nl/egouttage-de-la-rue-de-verviers_aide_14122022_23165582` -> `https://www.ebp.be/nl/category/geen-onderdeel-van-een-categorie/page/2/`
7. risk=`1.069` conf=`0.438` needs_review=`True` `https://www.ebp.be/nl/renovatie-leonardtunnel_mobiliteit-en-openbare-werken-age…` -> `https://www.ebp.be/nl/verklarende_woorden/awv-agentschap-wegen-en-verkeer/`
8. risk=`1.0646` conf=`0.4524` needs_review=`True` `https://www.ebp.be/fr/accordcadre-de-travaux-de-voiries-et-dégouttages-2022_vil…` -> `https://www.ebp.be/fr/formation-privee/laccord-cadre-vers-une-mise-en-oeuvre-so…`

## Quote Activity (draft / checkout / paid)
- Source `0edf6c7b-043d-4acd-8bdf-3986721b3642`: status=`draft`, created=`2026-03-13T12:56:06.331484+00:00`, checkout_created=`-`, paid=`-`
- Source `6cd2af17-a20f-49d9-b7f5-81eba870cdf7`: status=`draft`, created=`2026-03-13T15:31:07.674738+00:00`, checkout_created=`-`, paid=`-`

## Deep Preview Activity
- Source `0edf6c7b-043d-4acd-8bdf-3986721b3642`: present=`False`, status=`-`, preview_session_id=`-`, error=`-`
- Source `6cd2af17-a20f-49d9-b7f5-81eba870cdf7`: present=`False`, status=`-`, preview_session_id=`-`, error=`-`

## Funnel Verdict Per Session
### Session `0edf6c7b-043d-4acd-8bdf-3986721b3642`
- Preview queue attempt likely: `False` (status: `-`)
- Why not: `unknown_no_db_evidence` - All visible gates pass in current data, but no deep_match_previews row exists. Likely worker-side queue path did not persist (runtime logs unavailable).
- Preview gate checks: `{'feature_flag_enabled_now': True, 'source_is_url_only_non_preview': True, 'user_plan_is_free_now': True, 'meets_page_threshold': True, 'embeddings_configured_now': True, 'existing_preview_row_present': False, 'below_daily_cap_estimate': True, 'candidate_count_ge_4': True, 'candidate_source_count_ge_4': True, 'new_context_count_ge_2': True}`
- Checkout began: `False` (No checkout_created_at / checkout session ID; quote did not progress past current status.)
- Payment completed: `False` (No paid_at / paid status evidence.)

### Session `6cd2af17-a20f-49d9-b7f5-81eba870cdf7`
- Preview queue attempt likely: `False` (status: `-`)
- Why not: `unknown_no_db_evidence` - All visible gates pass in current data, but no deep_match_previews row exists. Likely worker-side queue path did not persist (runtime logs unavailable).
- Preview gate checks: `{'feature_flag_enabled_now': True, 'source_is_url_only_non_preview': True, 'user_plan_is_free_now': True, 'meets_page_threshold': True, 'embeddings_configured_now': True, 'existing_preview_row_present': False, 'below_daily_cap_estimate': True, 'candidate_count_ge_4': True, 'candidate_source_count_ge_4': True, 'new_context_count_ge_2': True}`
- Checkout began: `False` (No checkout_created_at / checkout session ID; quote did not progress past current status.)
- Payment completed: `False` (No paid_at / paid status evidence.)

## Concise Verdict
Analyzed 2 source session(s): preview evidence on 0, checkout began on 0, payment completed on 0. Missing requested sessions: none.
