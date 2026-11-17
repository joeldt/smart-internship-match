import json, os, time
import requests

# Source 2 : API Jooble (cle gratuite, 100 requetes par jour).
# Lancement manuel uniquement, au plus une fois par semaine (consigne du prof).
API_KEY = os.environ["JOOBLE_API_KEY"]
URL = f"https://jooble.org/api/{API_KEY}"

# Memes recherches ciblees que pour Adzuna, pour pouvoir comparer les deux sources.
QUERIES = ["stage développeur", "stage data", "stage devops"]
LOCATION = "France"
PAGES = 2  # 2 pages par recherche -> 6 requetes par lancement

calls_used = 0

def fetch(query, page, retries=3, backoff=5):
    global calls_used
    body = {"keywords": query, "location": LOCATION, "page": str(page)}
    for attempt in range(1, retries + 1):
        try:
            calls_used += 1
            resp = requests.post(URL, json=body, timeout=(10, 30))
            print(f"  '{query}' page {page}, tentative {attempt} : HTTP {resp.status_code}")
            if 400 <= resp.status_code < 500:
                raise RuntimeError(f"Erreur client HTTP {resp.status_code}, arret sans nouvelle tentative")
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            # On n'affiche que le type d'erreur : l'URL contient la cle.
            print(f"  echec reseau ({type(e).__name__})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Jooble injoignable apres {retries} tentatives")

def main():
    offers_by_key = {}
    per_query = {}
    n_raw = 0
    n_excluded = 0
    for query in QUERIES:
        fetched = 0
        total = 0
        for page in range(1, PAGES + 1):
            data = fetch(query, page)
            total = data.get("totalCount", 0)
            jobs = data.get("jobs", [])
            fetched += len(jobs)
            for o in jobs:
                title = (o.get("title") or "").strip()
                if "alternance" in title.lower():
                    n_excluded += 1  # meme regle que what_exclude=alternance chez Adzuna
                    continue
                key = (title.lower(), (o.get("company") or "").strip().lower())
                if key in offers_by_key:
                    if query not in offers_by_key[key]["matched_queries"]:
                        offers_by_key[key]["matched_queries"].append(query)
                else:
                    o["matched_queries"] = [query]
                    offers_by_key[key] = o
            if not jobs:
                break  # plus de resultats, inutile de consommer une requete de plus
        per_query[query] = {"total_available": total, "fetched": fetched}
        n_raw += fetched

    offers = list(offers_by_key.values())
    n_dup = n_raw - n_excluded - len(offers)
    for q, s in per_query.items():
        print(f"  '{q}' : {s['total_available']} disponibles, {s['fetched']} recuperees")
    print(f"Total : {n_raw} recuperees, {n_excluded} alternances exclues, "
          f"{n_dup} doublons retires, {len(offers)} offres uniques")
    print(f"Requetes API consommees pendant ce lancement : {calls_used}")

    os.makedirs("data/offers", exist_ok=True)
    with open("data/offers/raw_offers_jooble.json", "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "jooble",
            "queries": QUERIES,
            "location": LOCATION,
            "per_query": per_query,
            "n_raw": n_raw,
            "n_excluded_apprenticeship": n_excluded,
            "n_duplicates_removed": n_dup,
            "api_calls_used": calls_used,
            "offers": offers,
        }, f, indent=2, ensure_ascii=False)
    print("Ecrit : data/offers/raw_offers_jooble.json")

    if offers:
        print("Apercu de la premiere offre :")
        print(json.dumps(offers[0], ensure_ascii=False, indent=2)[:1500])

if __name__ == "__main__":
    main()
