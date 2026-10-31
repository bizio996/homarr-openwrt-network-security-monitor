# Runbook di collaudo — TrueNAS SCALE e OpenWrt

[Guida IT](README.md) | [English](../en/TESTING.md)

Questi sono **test manuali di integrazione** usati durante lo sviluppo, non test automatici CI. Le risposte dipendono dalla topologia e dal traffico. I comandi devono essere eseguiti sul sistema indicato. Non condividere password, session token, log completi, MAC o IP privati in issue pubbliche.

## TrueNAS: stato container e API

Esegui sulla shell **TrueNAS**, adattando il nome container.

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

**Atteso:** container in esecuzione, `/health` risponde `ok`, endpoint protetti con HTTP 200. Un errore 401 indica token mancante/errato; 502 o dati vecchi possono indicare RPC non disponibile.

## TrueNAS: filtri firewall e intervalli

```sh
read -r -s -p 'COLLECTOR_KEY: ' TOKEN; echo
for h in 1 24 168 720; do
  printf '%4sh: ' "$h"
  curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8788/firewall/explorer/$h" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("events",d.get("count"),"timeline",len(d.get("timeline",[])))'
done
curl -fsS -G -H "Authorization: Bearer $TOKEN" --data-urlencode 'hours=24' --data-urlencode 'src=192.0.2.0/24' 'http://127.0.0.1:8788/firewall/events' | python3 -m json.tool
unset TOKEN
```

**Atteso:** se confrontati nello stesso momento, i conteggi degli intervalli crescono o restano uguali (1h ≤ 24h ≤ 7d ≤ 30d). `192.0.2.0/24` è una subnet dimostrativa: normalmente produrrà zero risultati; sostituire localmente con una subnet pertinente. I filtri CIDR devono essere riportati in `filters`; campi IP vuoti = nessun filtro. `All`, `*` e regex non sono supportati nei campi IP.

## TrueNAS: syslog UDP 5514 e database persistente

```sh
sudo ss -lun | grep 5514
sudo tcpdump -ni any udp port 5514 -c 5
sudo docker logs --tail 100 openwrt-homarr-collector
```

`tcpdump` espone informazioni di rete: eseguirlo soltanto in ambiente fidato e non pubblicare output grezzo. Se Docker pubblica UDP 5514, i datagrammi devono raggiungere l'host. Per verificare il database senza installare `sqlite3` nel container, **fermare il collector o creare un backup SQLite consistente** e leggere *la copia* con Python `sqlite3` su TrueNAS. Evitare di copiare a caldo solo il file `.sqlite3` se SQLite usa WAL; preferire l'API `sqlite3.backup()` o interrompere il container. Dopo una ricreazione dell'app, lo storico deve rimanere disponibile attraverso il mount `/state`.

## OpenWrt: JSON-RPC, radio e interfacce

I comandi seguenti si eseguono **sulla shell del router OpenWrt**, non su TrueNAS.

```sh
ubus call system board
ubus call network.interface dump
ubus call network.interface.wan status
ubus call network.wireless status
ubus call iwinfo devices
ubus call iwinfo info '{"device":"phy0-ap0"}'
ubus call iwinfo assoclist '{"device":"phy0-ap0"}'
```

Sostituire `phy0-ap0` con una interfaccia realmente presente. L'assenza di radio 5 GHz, associazioni o determinati metodi `iwinfo` può essere legittima. Controllare da OpenWrt che l'account RPC dedicato abbia soltanto letture necessarie tramite ACL; non pubblicare il token di sessione.

## OpenWrt: log remoto e firewall

```sh
uci show system | grep -E 'log_(ip|port|proto|remote)'
logread | tail -n 40
fw4 check
logger -t HOMARR-FW-TEST 'DROP IN=br-test OUT= MAC= SRC=192.0.2.10 DST=192.0.2.20 PROTO=TCP SPT=54321 DPT=443'
```

**Atteso:** `fw4 check` completa senza errori di sintassi; il messaggio `HOMARR-FW-TEST` dovrebbe arrivare al collector se il syslog remoto è abilitato. È un **evento sintetico**, da escludere dai conteggi reali. Non abilitare regole DROP disabilitate soltanto per ottenere log e non aumentare indiscriminatamente il rate limit: i dati sono campionati.

## Homarr: collaudo finale

Importare tutti gli otto widget, uno alla volta. Controllare sorgente `default`, Bearer e endpoint richiesti. Verificare il grafico latenza (1h/24h/7d), Firewall Explorer (1h/24h/7d/30d, filtri, pagine) e External Traffic (ore locali Europe/Rome, IP sorgente/destinazione, porte, grafico). **Non includere `?` nel campo path se Homarr lo codifica `%3F`**. Errori `SubFetch.params.src contains an unsafe URL` richiedono parametri interni non ambigui. Nessuno dei test qui elencati dimostra che i widget v0.2.8 siano già collaudati nella tua istanza.

## Sicurezza e interpretazione

Non esporre TCP 8788 o UDP 5514 a Internet. Mantieni il router principale separato: eventi bloccati a monte non appaiono su OpenWrt. Nessun evento con IP pubblico **non equivale** ad assenza di traffico Internet o attacchi. Messaggi syslog non equivalgono a singole connessioni. Il percorso `.env` e `state/` non devono entrare in Git. Eseguire backup prima di ogni aggiornamento.
