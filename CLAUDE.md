# ScraperUniversale - Piano Architetturale

## Vision
Scraper B2B enterprise-grade per estrazione dati aziendali (nome, citta, email, telefono, sito web)
da directory industriali europee. Target: aziende produttrici e energivore.

## Fonti Dati (6 Fonti)

### TIER 1 - Alta qualita, email disponibili

#### 1. Europages (Fonte Primaria)
- **URL**: europages.co.uk / europages.com
- **Volume**: 2.6M+ aziende B2B europee
- **Dati**: nome, indirizzo, citta, telefono, fax, sito web, descrizione, settore, dipendenti, P.IVA
- **Email**: ALTA - campo obbligatorio per profili
- **Tecnica**: HTML parsing (Vue.js frontend, BS4+lxml)

#### 2. Kompass (Fonte Secondaria)
- **URL**: kompass.com / it.kompass.com
- **Volume**: 57M+ aziende in 70+ paesi
- **Dati**: nome, indirizzo, telefono, email, sito web, settore, classificazione NACE
- **Email**: MEDIO-ALTA
- **Tecnica**: HTML parsing con BeautifulSoup/lxml

#### 3. wlw.de - Wer Liefert Was
- **URL**: wlw.de
- **Volume**: 600K+ fornitori/produttori DACH (Germania, Austria, Svizzera)
- **Dati**: nome, indirizzo, telefono, email, sito web, P.IVA, contatti decision-maker
- **Email**: ALTA
- **Note**: Migliore fonte per produttori tedeschi (Germania = potenza manifatturiera EU)

#### 4. IndustryStock
- **URL**: industrystock.com / industrystock.it / industrystock.de
- **Volume**: 300K+ aziende industriali verificate, 3.16M+ prodotti, 17 lingue
- **Dati**: nome, indirizzo, telefono, email, sito web, settore
- **Email**: ALTA - "tutti i contatti liberamente disponibili"
- **Note**: Focus puro su industria/manifattura

### TIER 2 - Fonti specializzate

#### 5. Pagine Gialle (Italia)
- **URL**: paginegialle.it
- **Volume**: Tutte le aziende italiane
- **Dati**: nome, indirizzo, citta, telefono, sito web, email (parziale)
- **Email**: MEDIA - non tutti i listing hanno email
- **Note**: Copertura completa Italia, ricerca per citta

#### 6. CSEA Energivori (Lista Ufficiale)
- **URL**: energivori.csea.it
- **Volume**: ~4000 aziende energivore certificate in Italia
- **Dati**: Ragione Sociale, P.IVA, Codice Fiscale, classe agevolazione
- **Email**: NESSUNA direttamente - e una SEED LIST
- **Note**: Lista ufficiale governativa. Usare come base per poi arricchire con le altre fonti.
- **PDF**: Scaricabili direttamente dal sito CSEA

### Email Enrichment (Modulo Trasversale)
- Per ogni azienda trovata senza email, visita il sito web aziendale
- Estrae email con regex da: homepage, /contact, /contatti, /about, /impressum
- Prioritizza email del dominio aziendale vs generiche (gmail, etc.)

## Architettura

```
scraperuniversale/
├── CLAUDE.md                  # Questo file
├── requirements.txt           # Dipendenze Python
├── config.yaml                # Configurazione utente
├── main.py                    # CLI entry point (6 fonti)
├── .gitignore
├── scraper/
│   ├── __init__.py
│   ├── engine.py              # Core engine (session, proxy, rate limit, retry)
│   ├── europages.py           # Modulo Europages
│   ├── kompass.py             # Modulo Kompass
│   ├── wlw.py                 # Modulo wlw.de (DACH)
│   ├── industrystock.py       # Modulo IndustryStock
│   ├── paginegialle.py        # Modulo Pagine Gialle
│   ├── csea_energivori.py     # Modulo CSEA Energivori (PDF parser)
│   ├── email_enricher.py      # Arricchimento email da siti web
│   └── models.py              # Dataclass Company
├── export/
│   ├── __init__.py
│   ├── exporter.py            # CSV/JSON/Excel export
│   └── dedup.py               # Deduplicazione e validazione
└── output/                    # Directory output dati
```

## Stack Tecnologico
- **Python 3.10+**
- **httpx** - HTTP client asincrono (superiore a requests)
- **beautifulsoup4 + lxml** - HTML parsing veloce
- **rich** - CLI con progress bars e tabelle
- **openpyxl** - Export Excel con styling
- **pyyaml** - Configurazione YAML
- **fake-useragent** - Rotazione User-Agent

## Funzionalita
1. **6 fonti dati** - Europages, Kompass, wlw, IndustryStock, PagineGialle, CSEA
2. **Proxy rotation** - HTTP/SOCKS5
3. **Rate limiting intelligente** - Delay randomizzato + backoff esponenziale
4. **Retry automatico** - Con rotazione proxy su 429/403
5. **Deduplicazione** - Merge dati da fonti multiple (nome+citta)
6. **Export** - CSV, JSON, Excel (con header colorati)
7. **Filtri** - Per paese, citta, settore, keyword
8. **Validazione email** - Formato + blacklist falsi positivi
9. **CLI professionale** - Argparse + Rich (progress, tabelle, statistiche)
10. **Configurazione YAML** - Tutto configurabile

## Strategia Consigliata

### Per Aziende Energivore (Italia):
```bash
# 1. Scarica lista ufficiale CSEA (~4000 aziende)
python main.py -s csea_energivori -f excel

# 2. Arricchisci con Europages e Kompass
python main.py -k "energy intensive" "steel" "chemical" "glass" "cement" \
  -s europages kompass -c Italy -f excel

# 3. Completa con Pagine Gialle per citta specifiche
python main.py -k "produzione industriale" -s paginegialle \
  -c Milano Brescia Bergamo Torino -f excel
```

### Per Produttori Europei (Multi-Paese):
```bash
python main.py -k "manufacturing" "production" "Herstellung" \
  -s europages kompass wlw industrystock \
  -c Italy Germany France Spain -f excel --max 1000
```

### Full Power (Tutte le Fonti):
```bash
python main.py -k "steel" "chemical" "energy" "plastics" \
  -s europages kompass wlw industrystock paginegialle csea_energivori \
  -c Italy Germany France -f excel
```

## Fasi di Sviluppo
- [x] Fase 1: Ricerca fonti dati
- [x] Fase 2: Core engine + models
- [x] Fase 3: Europages scraper
- [x] Fase 4: Kompass scraper
- [x] Fase 5: wlw.de scraper
- [x] Fase 6: IndustryStock scraper
- [x] Fase 7: PagineGialle scraper
- [x] Fase 8: CSEA Energivori PDF parser
- [x] Fase 9: Email enrichment
- [x] Fase 10: Export + dedup pipeline
- [x] Fase 11: CLI + config

## Prossimi Miglioramenti
- [ ] Checkpoint/resume per scraping interrotto
- [ ] Playwright/Selenium per siti JS-heavy (fallback)
- [ ] Database SQLite/PostgreSQL per storage persistente
- [ ] Cross-reference CSEA lista con altre fonti per arricchimento automatico
- [ ] Dashboard web (Flask/FastAPI)
- [ ] API REST per integrazioni esterne
- [ ] Scheduling automatico (cron jobs)
- [ ] Notifiche Telegram/Slack su completamento
