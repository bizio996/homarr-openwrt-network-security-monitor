# Guida completa in italiano

[Panoramica progetto](../../README.md) · [English guide](../en/README.md) · [Checklist rilascio](RELEASE.md)

## Architettura

Il router OpenWrt espone dati in sola lettura via JSON-RPC `/ubus` e può inviare eventi firewall via syslog UDP 5514. Il collector Python su Docker/TrueNAS SCALE salva lo storico in SQLite (`/state/monitor.sqlite3`) e rende disponibili endpoint HTTP JSON autenticati. Homarr 2.x visualizza i dati tramite i widget personalizzati. Il collector gira come utente non privilegiato.

**Limite fondamentale:** se davanti al Cudy è presente un router dell'operatore, il traffico bloccato da quest'ultimo non è osservabile da OpenWrt. Per questo zero eventi con IP pubblico nei log non equivale a zero tentativi da Internet.

**[Runbook dei test TrueNAS e OpenWrt](TESTING.md)** — comandi e risultati attesi.

## 1. Requisiti

- OpenWrt con `rpcd`, `/ubus` e metodi wireless/`iwinfo` disponibili.
- TrueNAS SCALE con Custom App o altro host Docker sulla LAN, volume persistente e porte configurabili.
- Homarr 2.x con Custom Widget e sorgente HTTP con autenticazione Bearer.
- Accesso amministrativo al router **solo per impostare un account RPC dedicato in lettura e, facoltativamente, syslog remoto**.

## 2. OpenWrt: utente dedicato e logging

Crea un account RPC separato, con password robusta, senza usare `root`. Prendi come riferimento l'[ACL di esempio](../../openwrt/homarr-monitor-acl.json) e verifica i nomi di oggetti/metodi disponibili nel tuo firmware. Servono operazioni di lettura per `system`, `network.wireless`, `network.interface`, `network.device` e `iwinfo`; evita permessi di scrittura. Configura `OPENWRT_URL=http://IP_DEL_ROUTER` senza suffisso `/ubus` e senza percorso LuCI.

Per il monitoraggio firewall abilita, solo se desiderato, il syslog remoto UDP verso l'IP LAN del collector, porta 5514. Configura separatamente il logging delle zone firewall e un rate limit moderato: i messaggi sono campionati/limitati e **non rappresentano tutti i pacchetti**. Non abilitare regole DROP di cui non conosci l'effetto. Non aprire porte verso Internet per il solo monitoraggio.

## 3. Installazione su TrueNAS SCALE

Copia `collector/app.py`, `collector/Dockerfile` e `.env.example` in una directory di installazione. Duplica `.env.example` come `.env` e inserisci le credenziali locali **senza caricarle su GitHub**. Imposta `DEMO_MODE=false`, `SYSLOG_ENABLED=true` solo se syslog è attivo e, se necessario, `DISPLAY_TIMEZONE=Europe/Rome`.

Esempio sanificato:

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

Il file [`truenas/compose.yaml`](../../truenas/compose.yaml) è una **base da personalizzare**: cambia percorsi e mappatura delle porte in base all'host. Per il build dalla radice repository: `docker build -t openwrt-homarr-collector:local -f collector/Dockerfile collector`. Usa un volume permanente montato in `/state` e rendilo scrivibile all'utente `monitor` dentro il container. Non usare `chmod 777`. La porta TCP 8788 deve essere raggiungibile solo da Homarr/rete fidata; UDP 5514 soltanto dal router OpenWrt, se abilitato.

L'endpoint `/health` non richiede token e rivela solo informazioni minime; **gli altri endpoint richiedono** l'header `Authorization: Bearer <COLLECTOR_KEY>`. Non esporre il collector su Internet.

## 4. Importazione dei widget Homarr

In `widgets/` sono presenti otto file JSON da importare separatamente: Router, Wi-Fi, Latenza, Firewall, Storico Wi-Fi, Firewall Explorer, Network Health & Alerts, External Traffic Monitor. In ciascuno configura la sorgente `default`: cambia l'indirizzo dimostrativo `http://192.0.2.10:8788` con quello raggiungibile dalla tua installazione e configura il token tramite Homarr, **non modificando il JSON per includerlo in chiaro**.

Compatibilità nota: nel renderer JSX di Homarr alcuni metodi come `.map()`, `.slice()` e alcune formattazioni locali possono essere bloccati. L'asse X del grafico External Traffic usa etichette preformattate dal collector nel fuso `Europe/Rome`. Nei widget non aggiungere `?hours=...` direttamente al campo `path`: utilizzare `query` separatamente per evitare `%3F` e risposte 404. La ricerca manuale Firewall Explorer usa nomi dei parametri interni diversi da `src` per evitare validazioni URL insicure in `SubFetch`.

## 5. Endpoint disponibili

| Endpoint | Contenuto |
| --- | --- |
| `/health` | Stato minimo senza autenticazione |
| `/router` | Hardware, stato WAN, cambi WAN |
| `/wifi` | Radio, SSID e client associati |
| `/wifi/history?hours=24` | Storico client e associazioni |
| `/latency` | Riepilogo dei ping |
| `/latency/chart?hours=1` | Grafico 1/24/168 ore |
| `/firewall` | Contatori e classificazione degli eventi syslog |
| `/firewall/events?hours=24` | Eventi filtrati e paginati |
| `/firewall/explorer/24` | Grafici e ricerca firewall: 1/24/168/720 ore |
| `/firewall/timeline?hours=24` | Solo serie temporale |
| `/network/health` | Stato RPC, WAN, latenza, client e alert |
| `/external/traffic?hours=24` | IP pubblici/privati, sorgenti, subnet, porte, orari locali |

Per `/firewall/events` sono previsti `zone`, `action`, `direction`, `proto`, `reason`, `src`, `dst`, `port`, `hours`, `limit`, `offset`. Nei filtri IP sono supportati indirizzi esatti e subnet CIDR IPv4/IPv6; **campo vuoto = nessun filtro**. Non sono supportati `*`, `All` come testo digitato, né regex.

Test da terminale, senza incollare il token nella cronologia:

```sh
read -r -s -p 'Collector key: ' TOKEN; echo
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8788/network/health | python3 -m json.tool
curl -fsS -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8788/external/traffic?hours=24' | python3 -m json.tool
curl -fsS -G -H "Authorization: Bearer $TOKEN" --data-urlencode 'src=192.0.2.0/24' http://127.0.0.1:8788/firewall/events | python3 -m json.tool
unset TOKEN
```

## 6. Come interpretare i numeri

- **Eventi** = messaggi firewall registrati, non numero reale di pacchetti, tentativi ostili o connessioni.
- **Sorgente pubblica** = IP classificato come globalmente instradabile: non significa che sia un attacco.
- **Sorgente privata/speciale** = indirizzi RFC1918 o non pubblici, inclusi intervalli riservati.
- **WAN INPUT** = eventi rivolti al router; **FORWARD** = traffico in transito, secondo i prefissi presenti nei log.
- **Syslog silenzioso** = nessun evento osservato: non significa che il firewall abbia smesso di funzionare.
- **Client offline** = non visto recentemente, non necessariamente spento.
- **Orario del grafico** = etichette italiane del collector con `tzdata`, incluso passaggio CET/CEST.
- **TIM HUB+ a monte** = traffico bloccato dal TIM non compare nel Cudy; l'integrazione TIM è volontariamente rimandata.

## 7. Backup, aggiornamento e ripristino

Prima di ogni aggiornamento **arresta la Custom App** e salva `app.py`, `Dockerfile` e l'intera directory persistente `state`, contenente SQLite. Ricostruisci l'immagine e ricrea la Custom App senza eliminare il volume o cambiare le variabili `.env`. Testa `/health`, gli endpoint protetti e i singoli widget. Per tornare alla versione precedente ripristina codice/immagine e, se necessario in caso di migrazioni, il backup del database. Retention predefinita: 30 giorni.

## 8. Problemi comuni

- **401:** chiave Bearer errata o non configurata in Homarr.
- **404 su query:** il campo `path` del widget contiene `?` codificato come `%3F`; usare `query`.
- **Errore Docker permessi:** `app.py` deve essere leggibile dall'utente non privilegiato, `/state` scrivibile.
- **RPC non raggiungibile:** URL base sbagliato, errore nel nome utente, ACL insufficienti; `ubus` si esegue su OpenWrt, non su TrueNAS.
- **Zero eventi firewall:** controlla destinazione syslog UDP, firewall OpenWrt, `SYSLOG_ENABLED`, limiti di logging.
- **Zero sorgenti pubbliche:** normale se il router TIM blocca gli ingressi a monte.
- **Timestamp Unix nel grafico:** usa la coppia collector/widget aggiornata, controlla `timeline[].time` e la configurazione del fuso.
- **CIDR senza risultati:** verifica che la subnet compaia davvero nei log; lascia vuoto per cercare tutti gli IP.

## 9. Privacy, sicurezza e pubblicazione

Non pubblicare `.env`, database, MAC dei client, indirizzi privati reali, log completi, schermate con chiavi o file di backup. `.gitignore` protegge da aggiunte accidentali ma non corregge segreti già finiti nella cronologia Git: in tal caso **ruota le credenziali**. Gli esempi usano indirizzi riservati alla documentazione. Il progetto è ancora v0.2.8-alpha, non una v1.0.0 certificata. Vedi [checklist prima della pubblicazione](RELEASE.md).
