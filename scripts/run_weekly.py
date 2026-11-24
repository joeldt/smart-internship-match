"""Routine hebdomadaire en une commande (ou un double-clic sur semaine.bat).

1. Lance les deux collectes sur GitHub Actions (si la derniere date d'au moins 7 jours)
   et attend qu'elles soient terminees  -> necessite GitHub CLI (gh).
2. Recupere les nouvelles offres (git pull).
3. Enchaine le pipeline local : fusion, extraction IA (Ollama), matching, classement,
   sante du pipeline, monitoring.
4. Publie les resultats agreges (docs/) sur GitHub.

Options : --sans-collecte (ne relance pas les collectes), --forcer (collecte meme avant 7 jours),
          --sans-publication (ne pousse rien sur GitHub).
"""
import json, shutil, subprocess, sys, time
from datetime import datetime, timezone
import requests

WORKFLOWS = ["fetch_offers.yml", "fetch_offers_jooble.yml"]
RAW_FILES = ["data/offers/raw_offers.json", "data/offers/raw_offers_jooble.json"]
MIN_DAYS_BETWEEN_FETCHES = 7   # consigne du prof : une collecte par semaine au maximum
PIPELINE = [
    "scripts/merge_offer_sources.py",
    "scripts/extract_offers.py",
    "scripts/match_offers.py",
    "scripts/rank_offers.py",
    "scripts/build_pipeline_health.py",
    "scripts/check_search_health.py",
]


def etape(titre):
    print(f"\n=== {titre} ===", flush=True)


def lancer(commande, verifier=True):
    print("> " + " ".join(commande), flush=True)
    resultat = subprocess.run(commande)
    if verifier and resultat.returncode != 0:
        sys.exit(f"\nArret : la commande a echoue ({' '.join(commande)}).")
    return resultat.returncode


def jours_depuis_derniere_collecte():
    ages = []
    for path in RAW_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                fetched = json.load(f).get("fetched_at")
            date = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            ages.append((datetime.now(timezone.utc) - date).days)
        except (OSError, ValueError, AttributeError):
            ages.append(None)
    return None if None in ages else min(ages)


def dernier_run(workflow):
    sortie = subprocess.run(["gh", "run", "list", "--workflow", workflow, "--limit", "1",
                             "--json", "databaseId", "--jq", ".[0].databaseId"],
                            capture_output=True, text=True)
    return sortie.stdout.strip()


def collecter():
    if shutil.which("gh") is None:
        sys.exit("GitHub CLI (gh) n'est pas installe : 'winget install GitHub.cli' puis 'gh auth login'.\n"
                 "Ou relance avec --sans-collecte apres avoir lance les workflows a la main.")
    for workflow in WORKFLOWS:
        avant = dernier_run(workflow)
        lancer(["gh", "workflow", "run", workflow])
        # On attend que GitHub cree la NOUVELLE execution (pas la precedente, deja terminee)
        for _ in range(30):
            time.sleep(3)
            run_id = dernier_run(workflow)
            if run_id and run_id != avant:
                break
        else:
            sys.exit(f"La nouvelle execution de {workflow} n'apparait pas : verifier l'onglet Actions.")
        lancer(["gh", "run", "watch", run_id, "--exit-status", "--interval", "5"])


def verifier_ollama():
    try:
        requests.get("http://127.0.0.1:11434/api/version", proxies={"http": None, "https": None}, timeout=5)
    except requests.exceptions.RequestException:
        sys.exit("Ollama ne repond pas : lance l'application Ollama puis recommence.")


def main():
    args = set(sys.argv[1:])

    etape("1. Collecte des offres")
    age = jours_depuis_derniere_collecte()
    if "--sans-collecte" in args:
        print("Collecte ignoree (--sans-collecte).")
    elif age is not None and age < MIN_DAYS_BETWEEN_FETCHES and "--forcer" not in args:
        print(f"Derniere collecte il y a {age} jour(s) : moins de {MIN_DAYS_BETWEEN_FETCHES} jours, "
              f"on garde les offres actuelles (quotas). Utiliser --forcer pour passer outre.")
    else:
        collecter()

    etape("2. Recuperation des donnees")
    lancer(["git", "pull", "--rebase", "--autostash"])

    etape("3. Pipeline local")
    verifier_ollama()
    debut = time.time()
    for script in PIPELINE:
        etape(script)
        lancer([sys.executable, script])
    print(f"\nPipeline termine en {round((time.time() - debut) / 60, 1)} min.")

    if "--sans-publication" in args:
        print("\nPublication ignoree (--sans-publication).")
        return
    etape("4. Publication sur GitHub")
    lancer(["git", "add", "docs"])
    if lancer(["git", "diff", "--cached", "--quiet"], verifier=False) == 0:
        print("Rien de nouveau a publier.")
        return
    lancer(["git", "commit", "-m", f"Weekly update {datetime.now().strftime('%Y-%m-%d')}"])
    lancer(["git", "push"])
    print("\nTermine : le dashboard sera a jour dans 1 a 2 minutes.")


if __name__ == "__main__":
    main()
