# English installation and operations guide

[Project overview](../../README.md) · [Guida italiana](../it/README.md) · [Release checklist](RELEASE.md)

## Supported architecture

OpenWrt router (read-only JSON-RPC over `/ubus`, optional firewall remote syslog UDP 5514) → Python collector running as an unprivileged Docker user → persistent SQLite `/state/monitor.sqlite3` → authenticated HTTP JSON API → Homarr 2.x Custom Widgets. Initially developed on TrueNAS SCALE using an OpenWrt 24.10.x device; other hardware/firmware combinations require validation.

An upstream ISP router may perform NAT/firewall filtering. The collector cannot see packets blocked upstream. WAN can therefore have a private address; never equate the WAN zone with a publicly reachable Internet-facing interface.

## Prerequisites

- OpenWrt with working rpcd/ubus and `iwinfo` support where available.
- Docker on a trusted LAN host (such as TrueNAS SCALE), a persistent writable directory, and a reachable IP/port for Homarr.
- Homarr 2.x with Custom Widget HTTP data sources and Bearer authentication.
- Python collector image has `iputils` and `tzdata`; the source uses only the Python standard library.

**[TrueNAS and OpenWrt integration test runbook](TESTING.md)** — commands and expected results.

## 1. OpenWrt least-privilege access

Create a **dedicated** RPC user and install the minimal read-only ACL shown in [`openwrt/homarr-monitor-acl.json`](../../openwrt/homarr-monitor-acl.json). Actual rpcd account setup is dependent on router firmware and must be verified locally. Read-only methods include system board/info, wireless status, interfaces, devices and iwinfo association list. Never use root credentials or grant ubus write access just to get a widget working. Router base URL must be `http://ROUTER_IP` without `/ubus` or LuCI path fragments.

Firewall traffic statistics require logs. If desired, configure OpenWrt remote syslog **UDP 5514** to the collector's LAN IP and selectively enable firewall-zone logging with a modest rate limit. An empty syslog stream may be normal. Logging can contain private metadata.

## 2. Docker and TrueNAS SCALE

Copy `collector/app.py`, `collector/Dockerfile`, and `collector/.env.example` to a deployment directory. Make `.env` from `.env.example`, choose independent strong `OPENWRT_PASSWORD` and `COLLECTOR_KEY`, then set `DEMO_MODE=false`, `SYSLOG_ENABLED=true` only if syslog is configured, and optionally `DISPLAY_TIMEZONE=Europe/Rome`.

Example environment template:

```ini
OPENWRT_URL=http://192.0.2.1
OPENWRT_USER=monitor
OPENWRT_PASSWORD=REPLACE_WITH_RPC_PASSWORD
COLLECTOR_KEY=REPLACE_WITH_RANDOM_SECRET
DEMO_MODE=false
DB_PATH=/state/monitor.sqlite3
PORT=8788
SYSLOG_ENABLED=true
SYSLOG_PORT=5514
POLL_SECONDS=60
RETENTION_DAYS=30
PING_TARGETS=1.1.1.1,8.8.8.8
WEAK_RSSI_DBM=-70
DISPLAY_TIMEZONE=Europe/Rome
```

Adapt [`truenas/compose.yaml`](../../truenas/compose.yaml): **its paths are placeholders** and should point to your own deployment and persistent `state/` directory. Map TCP 8788; map UDP 5514 only if enabled. Build `docker build -t openwrt-homarr-collector:local -f collector/Dockerfile collector` from the repository root, or use Compose with a local build context. The container runs as non-root user `monitor`; make the host state directory writable by its UID/GID (verify inside your image). Never run `chmod 777` on secrets/state. Recreate the app after a rebuild while preserving the mounted state.

The `/health` endpoint is intentionally unauthenticated and returns minimal status. Every other endpoint requires `Authorization: Bearer <COLLECTOR_KEY>`. Protect access with trusted LAN/VPN firewall rules; HTTP alone is not safe across untrusted networks.

## 3. Homarr widgets

Import the eight JSON files from `widgets/` individually. Their `default` HTTP data source uses example URL `http://192.0.2.10:8788`, which is **not** a working address. Change it to your collector host and set the Bearer token in Homarr's secret/configuration UI. Never embed the token in a committed widget export. Keep the old widget until the imported version is confirmed.

Known Homarr renderer constraints: avoid unsupported `Array.map`, `Array.slice`, and locale formatting methods in the sandboxed JSX; the collector supplies local time labels for the external traffic chart. Keep URL query parameters in the source query configuration, **not** inside the `path` value (otherwise `?` may be encoded to `%3F`). The Firewall Explorer's manual SubFetch names source IP `sourceIp` rather than `src` to avoid an unsafe URL validation collision in Homarr.

## 4. API reference

The endpoints used by the repository are listed below. Unless stated otherwise, responses are JSON and authenticated.

| Endpoint | Description |
| --- | --- |
| `/health` | Basic process status, unauthenticated |
| `/router` | Router board/system, WAN and WAN state transitions |
| `/wifi` | Radio, SSID and currently associated stations |
| `/wifi/history?hours=24` | Known clients and appearance/disconnection history |
| `/latency` | Recent ICMP probe summary |
| `/latency/chart?hours=1` | Historical probe series (1, 24, 168 hours) |
| `/firewall` | Aggregated firewall logged-event counts and last syslog status |
| `/firewall/events?hours=24&src=192.0.2.0%2F24` | Paged firewall events; exact IP/CIDR source/destination filters |
| `/firewall/explorer/24` | Firewall timeline and filtered lists (1, 24, 168, 720 hours) |
| `/firewall/timeline?hours=24` | Timeline only |
| `/network/health` | WAN/RPC freshness, latency, weak RSSI and client/syslog alerts |
| `/external/traffic?hours=24` | IP-source classification, top IPs/subnets/ports, local-time timeline |

Firewall filter names include `zone`, `action`, `direction`, `proto`, `reason`, `src`, `dst`, `port`, `hours`, `limit`, `offset`. CIDR supports IPv4/IPv6; empty fields mean no filter, `*` and regexes are not supported. Window support differs by endpoint; do not assume arbitrary hours are accepted.

Examples (replace URL and use a private terminal token prompt):

```sh
read -r -s -p 'Collector key: ' TOKEN; echo
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8788/network/health | python3 -m json.tool
curl -fsS -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8788/external/traffic?hours=24' | python3 -m json.tool
curl -fsS -G -H "Authorization: Bearer $TOKEN" --data-urlencode 'src=192.0.2.0/24' 'http://127.0.0.1:8788/firewall/events' | python3 -m json.tool
unset TOKEN
```

## 5. Interpreting the results

- `public source`: address classified as globally routable; not proof of hostility.
- `private/special`: RFC1918 or reserved/non-global addresses; some may be ISP-side devices or local services.
- Firewall events represent **logged firewall decisions** and are subject to syslog loss and log-rate limiting, not traffic volume or connection count.
- `INPUT` points at traffic toward the router; `FORWARD` refers to traffic traversing it; classification depends on log prefixes and available metadata.
- A client counted offline was last observed earlier; it is not a proof of current power state.
- Unknown IPs, empty data and silent syslog should be treated as `unknown` or `no observed events`, not guaranteed safe.
- Time labels use `DISPLAY_TIMEZONE` (default Europe/Rome); Alpine image includes `tzdata` to handle CET/CEST.

## 6. Persistence and upgrade/rollback

The `/state` mount must persist across container rebuilds. **Stop the app and back up `app.py`, Dockerfile and the complete state directory** before deploying updates. Never delete `monitor.sqlite3` to fix widget errors. Rebuild with the same image tag and recreate the TrueNAS Custom App; verify health and each endpoint after the update. For rollback restore the previous code/image and original state backup together if a migration was involved. Respect UID/GID permissions of unprivileged container user.

Default SQLite retention is 30 days. Data may contain IP/MAC histories. Do not commit backups, screenshots with bearer keys, or raw SQL dumps.

## Troubleshooting

- **401:** Bearer token missing/incorrect; verify Homarr `default` source and `COLLECTOR_KEY` match. Do not paste secrets into issues.
- **404:** Ensure collector image is actually rebuilt; avoid query strings inside Homarr `path`.
- **Container `Permission denied`:** Verify `app.py` readable by non-root user and state directory writable; rebuild Docker image if ownership changed.
- **RPC failures:** Check router base URL format, RPC username spelling, credentials and read-only ACL. OpenWrt `ubus` commands run on the *router*, not on TrueNAS.
- **No firewall records:** Verify router syslog destination and UDP transport on trusted LAN, `SYSLOG_ENABLED`, firewall logging policy and rate limits.
- **Zero public-source events:** Expected when an upstream ISP router drops unsolicited traffic; verify top private sources and logged addresses instead.
- **Unix numbers on X axis:** Import updated external widget and collector together; test `timeline[].time` fields and timezone package.
- **No CIDR matches:** Try an IP known to be present in existing logs; empty fields disable filtering. Do not pass `*` or regular expressions.

## Privacy / limitations / roadmap

The TIM HUB+ / upstream ISP router's security logs are not integrated in this release. Do not unplug ONT, modify router NAT, or enable DMZ to obtain more events. The project is a local dashboard, not a replacement for network security monitoring. See the [release checklist](RELEASE.md) for the pre-1.0 validation tasks and Workshop preparation.
