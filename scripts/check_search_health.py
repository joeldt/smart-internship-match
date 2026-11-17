import csv, json, os, time
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Monitoring de ma recherche de stage (a lancer a la main, au moins 2 fois).
# Trois verifications, puis des chiffres AGREGES publies pour le dashboard
# (aucun nom d'entreprise : le suivi detaille reste dans data/tracking/, ignore par Git).
# ---------------------------------------------------------------------------

TRACKER = "data/tracking/applications.csv"
MATCHES = "data/offers/matches_all_local.json"
RAW_FILES = {"adzuna": "data/offers/raw_offers.json", "jooble": "data/offers/raw_offers_jooble.json"}
MONITORING_OUT = "docs/data/monitoring.json"
SUMMARY_OUT = "docs/data/tracking_summary.json"

FOLLOW_UP_DAYS = 10      # sans reponse depuis plus de 10 jours -> relancer
MAX_DATA_AGE_DAYS = 7    # collecte plus vieille qu'une semaine -> relancer les workflows
HIGH_SCORE = 0.3         # offre bien notee par mon KPI de matching
WAITING = {"envoyee", "relancee"}
RESPONDED = {"entretien", "refus", "acceptee"}


def parse_date(text):
    try:
        return datetime.strptime((text or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_tracker():
    if not os.path.exists(TRACKER):
        print(f"{TRACKER} absent : aucune candidature suivie.")
        return []
    with open(TRACKER, encoding="utf-8-sig") as f:  # utf-8-sig : accepte le BOM ajoute par PowerShell ou Excel
        return [row for row in csv.DictReader(f) if (row.get("entreprise") or "").strip()]


def check_follow_ups(apps, today):
    # Verification 1 : candidatures sans reponse depuis trop longtemps.
    due = []
    for a in apps:
        sent = parse_date(a.get("date_candidature"))
        if a.get("statut", "").strip() in WAITING and sent and (today - sent).days > FOLLOW_UP_DAYS:
            due.append((a["entreprise"], a.get("poste", ""), (today - sent).days))
    return due


def check_data_freshness(today):
    # Verification 2 : les donnees d'offres sont-elles recentes ?
    ages = {}
    for source, path in RAW_FILES.items():
        raw = load_json(path)
        fetched = parse_date((raw or {}).get("fetched_at"))
        ages[source] = (today - fetched).days if fetched else None
    return ages


def check_new_high_matches(apps):
    # Verification 3 : offres bien notees auxquelles je n'ai pas encore postule.
    data = load_json(MATCHES)
    if not data:
        return []
    applied_ids = {(a.get("id_offre") or "").strip() for a in apps}
    return [o for o in data["offers"] if o["score"] >= HIGH_SCORE and o["id"] not in applied_ids]


def summarize(apps):
    n = len(apps)
    by_status, by_channel, by_domain = {}, {}, {}
    delays = []
    for a in apps:
        status = a.get("statut", "").strip() or "inconnu"
        channel = a.get("canal", "").strip() or "inconnu"
        domain = a.get("domaine", "").strip() or "inconnu"
        responded = status in RESPONDED
        by_status[status] = by_status.get(status, 0) + 1
        for key, table in ((channel, by_channel), (domain, by_domain)):
            table.setdefault(key, {"envoyees": 0, "reponses": 0})
            table[key]["envoyees"] += 1
            table[key]["reponses"] += int(responded)
        sent, answered = parse_date(a.get("date_candidature")), parse_date(a.get("date_reponse"))
        if sent and answered:
            delays.append((answered - sent).days)
    n_responded = sum(by_status.get(s, 0) for s in RESPONDED)
    delays.sort()
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_applications": n,
        "n_responses": n_responded,
        "response_rate": round(n_responded / n, 3) if n else 0.0,
        "n_interviews": by_status.get("entretien", 0) + by_status.get("acceptee", 0),
        "median_response_days": delays[len(delays) // 2] if delays else None,
        "by_status": by_status,
        "by_channel": by_channel,
        "by_domain": by_domain,
    }


def main():
    today = date.today()
    apps = load_tracker()

    due = check_follow_ups(apps, today)
    ages = check_data_freshness(today)
    high = check_new_high_matches(apps)

    alerts = []
    print(f"=== Monitoring du {today.isoformat()} ===")
    print(f"\n1. Relances : {len(due)} candidature(s) sans reponse depuis plus de {FOLLOW_UP_DAYS} jours")
    for company, role, days in due:
        print(f"   - {company} ({role}) : {days} jours")
    if due:
        alerts.append(f"{len(due)} relance(s) a faire")

    print(f"\n2. Fraicheur des donnees (alerte au-dela de {MAX_DATA_AGE_DAYS} jours) :")
    for source, age in ages.items():
        label = "absente" if age is None else f"{age} jour(s)"
        print(f"   - {source} : {label}")
        if age is None or age > MAX_DATA_AGE_DAYS:
            alerts.append(f"collecte {source} a relancer")

    print(f"\n3. Offres avec un score >= {HIGH_SCORE} auxquelles je n'ai pas postule : {len(high)}")
    for o in high[:10]:
        print(f"   - {o['score']:.2f}  {o['title'][:60]} @ {o.get('company')}")
    if not high:
        alerts.append("aucune nouvelle offre bien notee")

    summary = summarize(apps)
    print(f"\nSuivi : {summary['n_applications']} candidatures, {summary['n_responses']} reponse(s) "
          f"({summary['response_rate']:.0%}), {summary['n_interviews']} entretien(s)")
    print("ALERTES : " + (" | ".join(alerts) if alerts else "aucune"))

    # Historique des executions (chiffres seulement) : prouve que le monitoring a tourne.
    history = load_json(MONITORING_OUT) or {"runs": []}
    history["runs"].append({
        "run_date": today.isoformat(),
        "n_applications": summary["n_applications"],
        "n_follow_ups_due": len(due),
        "data_age_days": ages,
        "n_high_match_not_applied": len(high),
        "alerts": alerts,
    })
    os.makedirs("docs/data", exist_ok=True)
    with open(MONITORING_OUT, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    with open(SUMMARY_OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nEcrit : {MONITORING_OUT} (execution n°{len(history['runs'])}) et {SUMMARY_OUT}")


if __name__ == "__main__":
    main()
