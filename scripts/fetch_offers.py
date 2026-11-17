import json, os, time
import requests

# Source : API Adzuna (remplace France Travail, indisponible).
# Compte gratuit : 25 appels de test au total -> lancement manuel, 1 appel par recherche.
APP_ID = os.environ["ADZUNA_APP_ID"]
APP_KEY = os.environ["ADZUNA_APP_KEY"]
COUNTRY = "fr"

# Une recherche par domaine vise (dev, data, DevOps) : plus precis qu'un seul
# "stage informatique", qui ramenait beaucoup d'offres hors sujet.
QUERIES = ["stage développeur", "stage data", "stage devops"]
COMMON_PARAMS = {
    "results_per_page": 50,
    "sort_by": "date",
    "what_exclude": "alternance",
}

calls_used = 0

def fetch_query(query, retries=3, backoff=5):
    global calls_used
    url = f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search/1"
    params = {"app_id": APP_ID, "app_key": APP_KEY, "what": query, **COMMON_PARAMS}
    for attempt in range(1, retries + 1):
        try:
            calls_used += 1
            resp = requests.get(url, params=params, timeout=(10, 30))
            print(f"  '{query}', tentative {attempt} : HTTP {resp.status_code}")
            if 400 <= resp.status_code < 500:
                # Cle invalide, quota depasse... : reessayer gaspillerait des appels.
                raise RuntimeError(f"Erreur client HTTP {resp.status_code}, arret sans nouvelle tentative")
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            # On n'affiche que le type d'erreur : le message complet contient l'URL avec la cle.
            print(f"  echec reseau ({type(e).__name__})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"Adzuna injoignable apres {retries} tentatives")

def main():
    offers_by_key = {}
    per_query = {}
    n_raw = 0
    for query in QUERIES:
        data = fetch_query(query)
        results = data.get("results", [])
        per_query[query] = {"total_available": data.get("count", 0), "fetched": len(results)}
        n_raw += len(results)
        for o in results:
            # Doublon = meme titre + meme entreprise (Adzuna agrege plusieurs sites).
            key = ((o.get("title") or "").strip().lower(),
                   ((o.get("company") or {}).get("display_name") or "").strip().lower())
            if key in offers_by_key:
                offers_by_key[key]["matched_queries"].append(query)
            else:
                o["matched_queries"] = [query]
                offers_by_key[key] = o

    offers = list(offers_by_key.values())
    for q, s in per_query.items():
        print(f"  '{q}' : {s['total_available']} disponibles, {s['fetched']} recuperees")
    print(f"Total : {n_raw} recuperees, {n_raw - len(offers)} doublons retires, {len(offers)} offres uniques")
    print(f"Appels API consommes pendant ce lancement : {calls_used}")

    os.makedirs("data/offers", exist_ok=True)
    with open("data/offers/raw_offers.json", "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "adzuna",
            "queries": QUERIES,
            "common_params": COMMON_PARAMS,
            "per_query": per_query,
            "n_raw": n_raw,
            "n_duplicates_removed": n_raw - len(offers),
            "api_calls_used": calls_used,
            "offers": offers,
        }, f, indent=2, ensure_ascii=False)
    print("Ecrit : data/offers/raw_offers.json")

if __name__ == "__main__":
    main()
