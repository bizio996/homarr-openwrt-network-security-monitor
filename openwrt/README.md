# OpenWrt setup / Configurazione OpenWrt

1. Use a dedicated rpcd account with only the read permissions needed. The adjacent JSON is an **example**, not a drop-in guarantee for every OpenWrt release; verify object/method names against your router and installed packages. Never reuse the root account.
2. Set `OPENWRT_URL=http://ROUTER_LAN_IP` (without `/ubus` or `/cgi-bin/luci/`). The collector adds `/ubus` itself.
3. Enable UDP remote syslog only if you want firewall events, and direct it to the collector's internal LAN host on port 5514. **Allow that port only from your router**; do not forward it externally.
4. Firewall zone logging and its rate limit are configured independently on OpenWrt. Enable only the desired logging; high log rates can affect flash and CPU load. Do not enable unknown DROP rules purely to produce logs.
5. If your ISP router is in front of OpenWrt, Internet packets dropped by the ISP router are not visible to the collector. No DMZ is required.

In italiano: usa un utente RPC in sola lettura, verifica gli ACL, imposta l'URL base senza suffissi, invia i log syslog soltanto alla LAN fidata, applica il rate limit ai log del firewall e non abilitare DMZ o port forwarding per il monitoraggio.
