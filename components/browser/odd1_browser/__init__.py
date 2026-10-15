"""odd1_browser: an agent's view of web pages.

text    stdlib fetch + HTML extraction, no JavaScript (fast, no Firefox needed)
render  Firefox (Selenium + geckodriver) behind a validating loopback proxy: rendered DOM, element ids, actions
"""
