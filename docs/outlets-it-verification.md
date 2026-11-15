# Italian outlet list verification

Run on 2026-11-14 with `python scripts/verify_outlets.py` against 56 candidate outlets (live network).

Homepage ok: 54, failed: 2. Outlets with a working feed: 35.
Final list `src/clipper/data/outlets/it.yaml`: 54 outlets, 32 with a feed.

## Adjustments after the run

- ilmessaggero.it: feed has no dated entries, using search fallback
- ilgazzettino.it: feed has no dated entries, using search fallback
- ilmattino.it: feed has no dated entries, using search fallback
- rinnovabili.it: dropped: homepage HTTP 403
- italiachecambia.org: dropped: homepage HTTP 403

## Per outlet (domain, homepage, feed found, newest entry)

| Domain | Homepage | Feed | Newest entry |
|---|---|---|---|
| ansa.it | ok  | https://www.ansa.it/rss.xml | 2026-11-14 |
| agi.it | ok  | https://www.agi.it:443/rss | 2026-11-14 |
| adnkronos.com | ok  | https://www.adnkronos.com/rss.xml | 2026-11-14 |
| askanews.it | ok  | https://askanews.it/feed/ | 2026-11-14 |
| corriere.it | ok  | - | - |
| repubblica.it | ok  | - | - |
| lastampa.it | ok  | https://www.lastampa.it/rss/copertina.xml | 2026-11-14 |
| ilsole24ore.com | ok  | https://www.ilsole24ore.com/rss/homepage.xml | 2026-11-14 |
| ilmessaggero.it | ok  | https://www.ilmessaggero.it/rss/home.xml | - |
| avvenire.it | ok  | - | - |
| ilfattoquotidiano.it | ok  | https://www.ilfattoquotidiano.it/feed/ | 2026-11-14 |
| rainews.it | ok  | - | - |
| tg24.sky.it | ok  | - | - |
| tgcom24.mediaset.it | ok  | https://www.tgcom24.mediaset.it/rss | 2026-11-14 |
| teleborsa.it | ok  | https://news.teleborsa.it/newsfeed.ashx | 2026-11-14 |
| ilgiornale.it | ok  | - | - |
| ilgazzettino.it | ok  | https://www.ilgazzettino.it/rss/home.xml | - |
| ilrestodelcarlino.it | ok  | https://www.ilrestodelcarlino.it/rss | 2026-11-14 |
| lanazione.it | ok  | https://www.lanazione.it/rss | 2026-11-14 |
| ilsecoloxix.it | ok  | - | - |
| unionesarda.it | ok  | - | - |
| ilmattino.it | ok  | https://www.ilmattino.it/rss/home.xml | - |
| gazzettadelsud.it | ok  | https://gazzettadelsud.it/upload/assets/rss/producer-nazionale-homepage.xml | 2026-11-14 |
| lagazzettadelmezzogiorno.it | ok  | https://feeds.feedburner.com/lagazzettadelmezzogiorno/viyi6z8dkwu | 2026-11-14 |
| gds.it | ok  | https://gds.it/upload/assets/rss/producer-sicilia-homepage.xml | 2026-11-14 |
| ilpiccolo.it | ok  | - | - |
| iltirreno.it | ok  | - | - |
| corrieredellumbria.it | ok  | - | - |
| ilcentro.it | ok  | - | - |
| ilgiorno.it | ok  | https://www.ilgiorno.it/rss | 2026-11-14 |
| canaleenergia.com | ok  | https://www.canaleenergia.com/feed/ | 2026-11-13 |
| qualenergia.it | ok  | https://www.qualenergia.it/feed/ | 2026-11-14 |
| rinnovabili.it | FAIL HTTP 403 | - | - |
| ecodallecitta.it | ok  | https://www.ecodallecitta.it/feed/ | 2026-11-13 |
| esg360.it | ok  | https://www.esg360.it/feed/ | 2026-11-13 |
| vita.it | ok  | https://www.vita.it/feed | 2026-11-14 |
| info-cooperazione.it | ok  | - | - |
| onuitalia.com | ok  | https://onuitalia.com/feed/ | 2026-11-14 |
| agensir.it | ok  | https://www.agensir.it/feed | 2026-11-14 |
| adcgroup.it | ok  | - | - |
| mediakey.it | ok  | https://mediakey.it/feed/ | 2026-11-13 |
| brand-news.it | ok  | https://brand-news.it/feed/ | 2026-11-13 |
| primapaginanews.it | ok  | https://www.primapaginanews.it/feed/ | 2026-11-14 |
| protectaweb.it | ok  | https://protectaweb.it/feed/ | 2026-11-13 |
| mase.gov.it | ok  | - | - |
| polito.it | ok  | https://www.polito.it/rss.xml | 2026-11-13 |
| sgi-italia.org | ok  | https://www.sgi-italia.org/feed/ | 2026-11-13 |
| ilfoglio.it | ok  | - | - |
| ilmanifesto.it | ok  | https://ilmanifesto.it/feed | 2026-11-13 |
| quotidianodelsud.it | ok  | https://www.quotidianodelsud.it/feed | 2026-11-14 |
| italiaoggi.it | ok  | - | - |
| liberoquotidiano.it | ok  | - | - |
| iltempo.it | ok  | - | - |
| open.online | ok  | https://www.open.online/feed | 2026-11-14 |
| greenreport.it | ok  | https://www.greenreport.it/?format=feed&amp;type=rss | 2026-11-13 |
| italiachecambia.org | FAIL HTTP 403 | - | - |
