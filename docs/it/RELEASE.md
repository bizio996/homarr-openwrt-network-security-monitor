# Checklist di pubblicazione GitHub e Homarr Workshop

- [ ] Verifica che `app.py` nel pacchetto corrisponda realmente alla versione in uso su TrueNAS.
- [ ] Controlla rendering di tutti gli 8 widget su Homarr 2.x, inclusi grafici, filtro CIDR e fuso orario.
- [ ] Verifica il comportamento degli endpoint `/network/health` e `/external/traffic` dal vivo.
- [ ] Controlla il principio del minimo privilegio nell'ACL OpenWrt.
- [ ] Verifica che Git e gli screenshot non contengano credenziali, MAC o IP privati reali.
- [ ] Personalizza esempi, compose e copyright della licenza prima del rilascio.
- [ ] Crea inizialmente il repository GitHub **privato**, revisiona il contenuto, quindi rendilo pubblico deliberatamente.
- [ ] Crea il tag `v0.2.8-alpha` dopo i test; non anticipare `v1.0.0`.
- [ ] Crea screenshot dimostrativi senza dati personali e prepara la scheda Homarr Workshop.
