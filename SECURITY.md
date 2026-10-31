# Security policy / Sicurezza

**Supported snapshot:** v0.2.8-alpha (best-effort; no security SLA).

Report vulnerabilities privately to the repository owner using GitHub's private vulnerability reporting if enabled. Do not disclose sensitive device addresses, passwords, bearer tokens, syslog samples containing personal data, or unredacted databases in public issues.

Minimum deployment guidelines:
- Restrict access to port 8788 to Homarr and trusted hosts on LAN or VPN; never publish it to the Internet.
- Restrict UDP syslog port 5514 to router IPs only. Syslog is unauthenticated UDP; consider events untrusted.
- Use a dedicated read-only rpcd account, strong independent secrets and scoped ACLs.
- Keep `/state` persistent, writable only by the container account, and excluded from Git.
- Prefer HTTPS via a trusted internal reverse proxy if credentials cross untrusted links.
- Keep router, Python image and host patched. The collector is not a replacement for a firewall or IDS.
- If a token was ever committed, revoke/rotate it; deleting the file does not remove it from Git history.

Versione italiana: limita 8788 e 5514 alla LAN/VPN, non pubblicare `.env` o SQLite, usa credenziali RPC dedicate e permessi minimi e ruota immediatamente eventuali segreti divulgati.
