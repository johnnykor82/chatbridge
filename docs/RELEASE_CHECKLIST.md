# GitHub release checklist

- [x] Independent Git repository with commits
- [x] License and security disclosures
- [x] English README and usage examples
- [x] Reproducible `uv.lock` dependencies
- [x] GitHub Actions for Python 3.11 / 3.13
- [x] Packaging as wheel and sdist
- [x] Unit/integration tests green locally
- [x] 10 simultaneous requests plus 11th queued in live test
- [x] Continuing an existing ChatGPT conversation over native HTTP
- [x] Read-only session history synchronization
- [x] Reference documentation for every implemented HTTP endpoint
- [x] Installer from Git checkout: Python environment, locked dependencies, dedicated managed Chrome and login instructions (requires uv and Chrome)
- [x] Installer installation/upgrade/uninstall and isolated mock LaunchAgent boot verified on development Mac
- [ ] Full clean-Mac install, fresh ChatGPT login and cold reboot verified
- [ ] Verify full history cursor order and imported branch handling
- [ ] Remove developer-specific identifiers from public examples and tests
- [x] Browser readiness, authentication and service status available at /ready and /v1/system/status
- [x] Standalone installer tests do not modify the running Mac Control, Watcher or primary ChatBridge
- [x] One live MCP approval and tool-execution test on target account (conversation scope; Mac Control command exited 0)
- [ ] Real 10+ minute multi-tool completion without early finish
- [x] Managed Chrome and gateway SIGKILL recovery tested on development Mac; isolated installer LaunchAgent also restarted after SIGKILL
- [ ] Fresh standalone install's new Chrome profile independently signed into ChatGPT
- [x] Standalone ChatBridge does not depend on Mac Control; its own installer
      manages its runtime, browser profile and LaunchAgent independently
- [x] English and Russian README entry points link to one another at top
- [x] MIT LICENSE already included in standalone source
- [ ] Validate Browser API v1 from a second Mac / new Chrome profile
- [ ] Run publishing secret and provenance audit on the exact public Git history
- [ ] Link the published ChatBridge tag/repository from the Mac Control
      dependency installer (do not embed ChatBridge source or credentials)
- [ ] Publish standalone ChatBridge beta before Mac Control beta
- [ ] Exact candidate reviewed, CI passed, and a new experimental release tagged

**Do not claim production-ready until all blocking checkboxes are satisfied.**
Publishing the current source as an experimental alpha is technically possible
if the limitations are clearly disclosed.
