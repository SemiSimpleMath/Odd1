# browser
An agent's view of web pages: render a JavaScript site in Firefox and act on it by element id, without guessing at the screen.
Milestone: ★ (any app, through interfaces agents write themselves): the browser's own layout is the screen model for web pages.

## Use
- Agents: the MCP server `browser` (registered for every agent). One Firefox per server, kept between calls:
  `browser_open(url, wait_ms)`, `browser_view(screenshot)`, `browser_click(id, allow_form_post)`,
  `browser_type(id, text)`, `browser_select(id, value)`, `browser_submit(id, allow_form_post)`,
  `browser_find(query)`, `browser_fetch(url)` (no JavaScript, no Firefox), `browser_close()`.
  Every Firefox call returns the page view: title, text, headings, links, `elements` (interactive ones, ids like
  `el:0003`, given in document order and kept while the page lives) and `stable_ids` (`id:<html id>`).
- Command line: `odd1-browser text URL` (fetch, no JavaScript); `odd1-browser render URL [--wait-ms N]
  [--screenshot] [--visible] [--actions-json '[{"id":"id:name","action":"type","value":"Ada"}]'] [--allow-form-post]`.
  Prints JSON in the shape of `odd1_browser/schema-v1.json`.
- Python: `from odd1_browser.render import FirefoxSession` (`open`, `view`, `act`, `close`; a context manager),
  `rendered_browse(url, actions=...)` for one shot, `odd1_browser.text.browse(url)` and `BrowserSession` for
  the no-JavaScript browser with history.

## Policy
URLs are literal-loopback `http` only (127.0.0.1, ::1): the sites agents build and run on the machine. Hostnames are
refused (no DNS-rebinding gap). Firefox's traffic all goes through a per-session validating proxy: loopback GETs,
no CONNECT/HTTPS, no downloads, every redirect revalidated, 1 MB per response, 16 workers. A form POST passes only
when the caller set `allow_form_post` on that action, to the opened page's origin, once, with exactly the
URL-encoded body the form showed. This is application policy, not an OS sandbox.

## Tests
`python -m pytest components/browser/tests -q` anywhere (offline: text browser, proxy policy, actions, session
and MCP tools with a fake WebDriver). Real-Firefox tests, the schema contract (jsonschema), the MCP tests (mcp) and
process-group cleanup (POSIX) skip with a reason where their dependency is missing; `odd1-browser-test` on the Odd1
Desktop runs them all.

## Next
- Follow navigation in a session: `allowed_post_origin` is fixed at `browser_open`; decide whether a followed link
  to another loopback port may then submit forms there.
- Change events: tell the agent what changed since its last view (added/removed elements, text diff) instead of
  resending the whole page.
- Feed the screen-model service: map element ids to screen rectangles (`getBoundingClientRect` plus window
  position) so a visible Firefox joins the desktop's ID render.
- Beyond loopback: if Odd1 ever allows web access, put it behind the same proxy with a per-task allowlist.
- Resource limits for Firefox (memory, tasks, runtime) belong to the machine's process manager, not this component.
