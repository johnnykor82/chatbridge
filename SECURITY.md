# Security and autonomy

ChatBridge is an **unofficial automation gateway**, not an official OpenAI product.

## High-risk permission model

When ChatGPT displays an MCP approval, the gateway is designed to choose
**allow for this conversation**, falling back to **allow once**. By design,
normal approvals require no additional user interaction or notification.
Consequently, a model can invoke connected tools with the full permissions
of the authenticated user, potentially including terminal commands, file
modification, external messaging and other consequential actions.

Only run this software after explicitly accepting those risks. Review connected
MCP tools and their access. The audit table stores automatically applied
approvals. No policy switch silently reduces those capabilities.

## Deployment

- Bind to `127.0.0.1` by default; never expose an unauthenticated HTTP
  listener beyond a trusted machine. Set `CHATBRIDGE_API_KEY` for remote use.
- Isolate the Chrome profile used by this gateway. Do not commit browser
  storage, cookies, bearer tokens, database contents or logs.
- Account session state stays in Chrome. Web APIs are undocumented and may
  change, reject traffic or rate-limit requests.
- Do not interpret a pending tool cycle as a completed response.
- Keep backups of persistent databases; avoid logging sensitive prompts or
  authorization headers.
- Report vulnerabilities privately to the maintainers of your fork; do not
  post active credentials in GitHub Issues.

Browser requests with an Origin header are accepted only from the gateway's
exact scheme and Host (including port). Foreign, null and duplicate origins
are rejected. Clients without Origin still have keyless loopback access by
default; set CHATBRIDGE_API_KEY for local authentication. Origin checks do not
protect against other local processes or replace authentication.

Keyless loopback mode also validates Host against localhost, 127.0.0.1 or
[::1] and the actual listener port, including requests without Origin, to
reject DNS rebinding. With a configured key, deployments may use other Host
names; the bearer key remains required and Origin must still match exactly.
