# Changelog

## 1.0.3

- The default server is `https://my.prostocam.fyi`: ProstoCAM left the name `new.prosto.cam`.
- A saved connection to `new.prosto.cam` (with or without `https://` and a trailing slash) moves to
  `https://my.prostocam.fyi` by itself on update (config entry 1.1 → 1.2). The token stays, no new code is needed.
  A server of your own is not changed. Absolute addresses in the replies of the server are used as they come.

## 1.0.2

- A server in trouble is no longer taken for a revoked token. On 18.11.2026 the database of the server was
  recreated, and for six minutes the token check answered `401 token_invalid`; 1.0.1 believed the first 401, raised
  "Reconnect ProstoCAM" and stopped polling, although the connection was alive.
- `401 integration_revoked` (the server's word for a connection removed in the web account) asks for a new code at
  once.
- Any other `401` asks for a new code only when it lasts: 3 refusals in a row with no accepted heartbeat between, the
  first one at least 10 minutes ago. Until then the bridge retries with a backoff, the event channel reconnects and the
  entities are unavailable. Home Assistant that starts during such a refusal loads and keeps retrying.
- Network errors, 5xx and `503 temporarily_unavailable` (with `Retry-After`): retries, entities unavailable, no repair.
- Heartbeats go on while a request for a new code is shown; the first accepted one withdraws the request and reopens
  the event channel.
- Diagnostics: `auth_refusals_in_a_row`, `server_unreachable`.

## 1.0.1

- Errors of actions in plain words: a refusal of the server is a `ServiceValidationError` with a translated phrase
  (uk/ru/en/bg), no 500 and no traceback in the log of Home Assistant.
