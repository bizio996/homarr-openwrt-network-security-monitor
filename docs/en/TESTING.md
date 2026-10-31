# Integration test runbook — TrueNAS SCALE and OpenWrt

[English guide](README.md) | [Italiano](../it/TESTING.md)

The following are **manual integration checks** used during development, not CI unit tests. Responses depend on the network and observed traffic. Run commands on the **specified host**. Never disclose passwords, bearer tokens, raw logs, MAC addresses, or local private addresses in public issues.

## TrueNAS: container and API

Run on the **TrueNAS shell**, adjusting container name if needed.

```sh
sudo docker ps --filter name=openwrt-homarr-collector
sudo docker logs --tail 80 openwrt-homarr-collector
curl -fsS http://127.0.0.1:8788/health | python3 -m json.tool
read -r -s -p 'COLLECTOR_KEY: ' TOKEN; echo
for ep in router wifi latency firewall wifi/history network/health 'external/traffic?hours=24'; do
  printf '%s: ' "$ep"
  curl -sS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8788/$ep"
done
unset TOKEN
```

**Expected:** container running, `/health` reports `ok`, authenticated endpoints return HTTP 200. HTTP 401 indicates missing/wrong credentials; stale data may indicate RPC failure.

## TrueNAS: firewall ranges and CIDR queries

```sh
read -r -s -p 'COLLECTOR_KEY: ' TOKEN; echo
for h in 1 24 168 720; do
  printf '%4sh: ' "$h"
  curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8788/firewall/explorer/$h" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("events",d.get("count"),"timeline",len(d.get("timeline",[])))'
done
curl -fsS -G -H "Authorization: Bearer $TOKEN" --data-urlencode 'hours=24' --data-urlencode 'src=192.0.2.0/24' 'http://127.0.0.1:8788/firewall/events' | python3 -m json.tool
unset TOKEN
```

**Expected:** when queried close together, interval event counts are nondecreasing (1h ≤ 24h ≤ 7d ≤ 30d). `192.0.2.0/24` is a documentation-only subnet and will usually return no records. Substitute a relevant subnet **locally**. Exact IP or CIDR filters should appear in `filters`; an empty field means any IP. Literal `All`, `*`, and regular expressions are not supported.

## TrueNAS: UDP syslog and SQLite persistence

```sh
sudo ss -lun | grep 5514
sudo tcpdump -ni any udp port 5514 -c 5
sudo docker logs --tail 100 openwrt-homarr-collector
```

Packet captures may disclose network data; never post raw output publicly. Check UDP port publication and packets arriving from OpenWrt. Before inspecting SQLite manually, **stop the collector or take a consistent SQLite backup** (`sqlite3.backup()`); do not copy only a live SQLite file if WAL is in use. Check history survives container recreation because `/state` is a persistent mount.

## OpenWrt: RPC, wireless and interfaces

Run these commands **on the OpenWrt router shell**, not on TrueNAS.

```sh
ubus call system board
ubus call network.interface dump
ubus call network.interface.wan status
ubus call network.wireless status
ubus call iwinfo devices
ubus call iwinfo info '{"device":"phy0-ap0"}'
ubus call iwinfo assoclist '{"device":"phy0-ap0"}'
```

Replace `phy0-ap0` with a real wireless interface. Some radios or `iwinfo` methods may be unavailable. Ensure the collector uses a dedicated read-only RPC user, and do not share session tokens.

## OpenWrt: firewall and remote logging

```sh
uci show system | grep -E 'log_(ip|port|proto|remote)'
logread | tail -n 40
fw4 check
logger -t HOMARR-FW-TEST 'DROP IN=br-test OUT= MAC= SRC=192.0.2.10 DST=192.0.2.20 PROTO=TCP SPT=54321 DPT=443'
```

**Expected:** `fw4 check` has no syntax errors and the `HOMARR-FW-TEST` message reaches the collector if UDP syslog is configured. It is a **synthetic event**, excluded from real counts. Do not activate disabled DROP rules just to generate logs, and retain sensible logging rate limits.

## Homarr acceptance testing

Import all eight widgets one at a time. Confirm the `default` source, Bearer key and request paths. Verify latency charts (1h/24h/7d), Firewall Explorer (1h/24h/7d/30d, pagination and filters), and External Traffic (Europe/Rome labels, sources/destinations, ports and charts). Avoid `?` in Homarr's `path` field if it is encoded as `%3F`; use separate query parameters. The `SubFetch.params.src contains an unsafe URL` warning requires unambiguous internal prop names. This runbook **does not claim the v0.2.8 widget has already been tested on your installation**.

## Security and interpretation

Never expose collector TCP 8788 or UDP 5514 to the public Internet. An upstream router may block traffic that OpenWrt never observes. Zero public-source events does not mean zero attacks. Firewall log entries are not packet-level or connection-level totals. Do not commit `.env` or `state/`. Back up before upgrades.
