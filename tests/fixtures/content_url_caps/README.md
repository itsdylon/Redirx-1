# Content URL Cap Test CSVs

These fixtures target the default content cap of 5000 URLs per file.

Use `POST /api/process` with multipart form fields:
- `old_csv` (file)
- `new_csv` (file)
- `pipeline_type=content`

Expected outcomes (non-launch plan):
- `within_cap_old_5000.csv` + `within_cap_new_5000.csv` -> accepted (not 422)
- `over_cap_old_5001.csv` + `over_cap_new_1.csv` -> HTTP 422, `reason_code=content_old_url_cap_exceeded`
- `over_cap_old_1.csv` + `over_cap_new_5001.csv` -> HTTP 422, `reason_code=content_new_url_cap_exceeded`
- `over_cap_both_old_5001.csv` + `over_cap_both_new_5001.csv` -> HTTP 422, `reason_code=content_both_url_caps_exceeded`

Response payload fields for cap failures include:
- `code`
- `reason_code`
- `old_url_count`
- `new_url_count`
- `max_old_urls`
- `max_new_urls`

Note: Launch-plan users are forced to `url_only`, which bypasses content cap enforcement.
