import json, os, time
import requests

# Source : API Adzuna (remplace France Travail, indisponible).
# Compte gratuit en attente d'activation : 25 appels de test au total.
# Lancement manuel uniquement, au plus une fois par semaine.
APP_ID = os.environ["ADZUNA_APP_ID"]
APP_KEY = os.environ["ADZUNA_APP_KEY"]
COUNTRY = "fr"
PAGES = 2  # 2 appels de 50 offres = 100 offres maximum par lancement

SEARCH_PARAMS = {
    "what": "stage informatique",
    "results_per_page": 50,
    "sort_by": "date",
}

calls_used = 0

def fetch_page(page, retries=3, backoff=5):
    global calls_used
    url = f"https://api.adzuna.com/v1/api/jobs/{COUNTRY}/search/{page}"
    params = {"app_id": APP_ID, "app_key": APP_KEY, **SEARCH_PARAMS}
    for attempt in range(1, retries + 1):
        try:
            calls_used += 1
            resp = requests.get(url, params=params, timeout=(10, 30))
            print(f"  page {page}, tentative {attempt} : HTTP {resp.status_code}")
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
    offers = []
    total = 0
    for page in range(1, PAGES + 1):
        data = fetch_page(page)
        total = data.get("count", 0)
        results = data.get("results", [])
        offers.extend(results)
        if len(results) < SEARCH_PARAMS["results_per_page"]:
            break  # plus de resultats, inutile de consommer un appel de plus

    print(f"Adzuna annonce {total} offres correspondantes ; {len(offers)} recuperees")
    print(f"Appels API consommes pendant ce lancement : {calls_used}")

    os.makedirs("data/offers", exist_ok=True)
    with open("data/offers/raw_offers.json", "w", encoding="utf-8") as f:
        json.dump({
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "adzuna",
            "search_params": SEARCH_PARAMS,
            "total_available": total,
            "api_calls_used": calls_used,
            "offers": offers,
        }, f, indent=2, ensure_ascii=False)
    print("Ecrit : data/offers/raw_offers.json")

    if offers:
        print("Apercu de la premiere offre :")
        print(json.dumps(offers[0], ensure_ascii=False, indent=2)[:1500])

if __name__ == "__main__":
    main()
