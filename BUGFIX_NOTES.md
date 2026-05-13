# Bug Report: LinkedIn Profile ID Extraction Failures

### Bug 1: Brotli-Compressed Response Not Decoded

**Root Cause**
The browser extension captures raw LinkedIn session headers, including `Accept-Encoding: gzip, deflate, br`. When these headers were forwarded verbatim in server-side requests, LinkedIn responded with brotli-compressed content. `httpx` requires the `brotlicffi` (or `brotli`) package to decompress brotli responses — without it, `response.text` returned garbled binary data (raw compressed bytes decoded as UTF-8, producing replacement characters `�`).

**Solution**
- Added `brotlicffi>=1.2.0.1` to `backend/pyproject.toml`.
- In both fetch sites, overrode `Accept-Encoding` to `gzip, deflate` before making the request, preventing LinkedIn from sending brotli in the first place. This is the more robust fix since it removes the dependency on any brotli package being present.

**Files Changed**
- `backend/pyproject.toml` — added `brotlicffi` dependency
- `backend/app/api/v1/utils.py` — override `accept-encoding` header before fetching profile HTML
- `backend/app/linkedin/utils/profile_id_extractor.py` — same override

---

### Bug 2: HTML Parsing Pattern Broken by LinkedIn Frontend Change

**Root Cause**
Both extraction functions searched for `<code id="bpr-guid-...">` blocks that LinkedIn previously used to embed server-side rendered data (GraphQL responses) into the page. LinkedIn has since migrated to a new frontend architecture that no longer emits these blocks. The profile data is now embedded as minified inline JSON inside JavaScript strings, where all double-quotes are backslash-escaped (`\"`). Example from the actual HTML:

```
vanityName\":\"anamariam\",\"profileUrn\":\"urn:li:fsd_profile:ACoAAAJ4m48BGgrDowFPwo-kDM-3uxAQ5lm1MGg\"
```

**Solution**
Replaced the `bpr-guid` regex with a pattern that matches the new inline JSON structure:

```python
pattern = (
    r'vanityName\\":\\"' + re.escape(vanity_name) + r'\\"'
    r'.{0,400}?'
    r'profileUrn\\":\\"urn:li:fsd_profile:([A-Za-z0-9_-]+)\\"'
)
match = re.search(pattern, html, re.DOTALL)
```

The `{0,400}?` lazy quantifier handles cases where LinkedIn places other fields (e.g. `profileFormEntryPoint`, `trackingId`) between `vanityName` and `profileUrn` in the same JSON object.

**Files Changed**
- `backend/app/api/v1/utils.py` — replaced `_extract_profile_id_from_html_content()`, removed unused `json` and `html.unescape` imports
- `backend/app/linkedin/utils/profile_id_extractor.py` — replaced `_extract_profile_id_from_html()`
