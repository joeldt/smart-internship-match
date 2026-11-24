import csv, json, os, time
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Monitoring de ma recherche de stage (a lancer a la main, au moins 2 fois).
# Trois verifications, puis des chiffres AGREGES publies pour le dashboard
# (aucun nom d'entreprise : le suivi detaille reste dans data/tracking/, ignore par Git).
# ---------------------------------------------------------------------------

TRACKER = "data/tracking/applications.csv"
MATCHES = "data/offers/matches_all_local.json"
RANKING = "data/offers/ranking_all_local.json"
PREFS = "data/profile/preferences.json"
RAW_FILES = {"adzuna": "data/offers/raw_offers.json", "jooble": "data/offers/raw_offers_jooble.json"}
MONITORING_OUT = "docs/data/monitoring.json"
SUMMARY_OUT = "docs/data/tracking_summary.json"

FOLLOW_UP_DAYS = 10      # sans reponse depuis plus de 10 jours -> relancer
MAX_DATA_AGE_DAYS = 7    # collecte plus vieille qu'une semaine -> relancer les workflows
HIGH_SCORE = 0.3         # ancien seuil (score de competences seul), utilise si le classement manque
TOP_N = 12               # offres du haut du classement multicritere
# Statuts possibles dans le tracker :
#   envoyee, relancee, refus, entretien, refus_apres_entretien, acceptee
WAITING = {"envoyee", "relancee"}
RESPONDED = {"entretien", "refus", "refus_apres_entretien", "acceptee"}
INTERVIEWED = {"entretien", "refus_apres_entretien", "acceptee"}
# Tranches de note globale (classement multicritere) pour valider le KPI par les resultats.
SCORE_BANDS = [("Bien notée (≥ 0,60)", 0.60), ("Moyenne (0,45 à 0,60)", 0.45), ("Faible (< 0,45)", -1)]


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
    # Le delai part de la derniere relance s'il y en a eu une, sinon de l'envoi.
    due = []
    for a in apps:
        last_contact = parse_date(a.get("date_relance")) or parse_date(a.get("date_candidature"))
        if a.get("statut", "").strip() in WAITING and last_contact and (today - last_contact).days > FOLLOW_UP_DAYS:
            kind = "2e relance" if parse_date(a.get("date_relance")) else "1re relance"
            due.append((a["entreprise"], a.get("poste", ""), (today - last_contact).days, kind))
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
    # Verification 3 : offres du haut de mon classement multicritere auxquelles je n'ai pas postule.
    applied_ids = {(a.get("id_offre") or "").strip() for a in apps}
    ranking = load_json(RANKING)
    if ranking:
        top = ranking["offers"][:TOP_N]
        return [{**o, "score": o["global_score"]} for o in top if o["id"] not in applied_ids]
    data = load_json(MATCHES)   # repli : ancien critere (competences seules)
    if not data:
        return []
    return [o for o in data["offers"] if o["score"] >= HIGH_SCORE and o["id"] not in applied_ids]


def iso_week(d):
    year, week, _ = d.isocalendar()
    return f"{year}-S{week:02d}"


def summarize(apps):
    n = len(apps)
    ranking = load_json(RANKING) or {"offers": []}
    global_scores = {o["id"]: o["global_score"] for o in ranking["offers"]}
    prefs = load_json(PREFS) or {}
    by_week, by_band = {}, {label: {"envoyees": 0, "reponses": 0} for label, _ in SCORE_BANDS}
    n_linked = 0
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
        # Rythme : candidatures envoyees et relances faites, par semaine
        if sent:
            by_week.setdefault(iso_week(sent), {"envoyees": 0, "relances": 0})["envoyees"] += 1
        relance = parse_date(a.get("date_relance"))
        if relance:
            by_week.setdefault(iso_week(relance), {"envoyees": 0, "relances": 0})["relances"] += 1
        # Validation du KPI : taux de reponse selon la note de l'offre (si l'offre vient du pipeline)
        offer_id = (a.get("id_offre") or "").strip()
        if offer_id in global_scores:
            n_linked += 1
            band = next(label for label, low in SCORE_BANDS if global_scores[offer_id] >= low)
            by_band[band]["envoyees"] += 1
            by_band[band]["reponses"] += int(responded)
    n_responded = sum(by_status.get(s, 0) for s in RESPONDED)
    delays.sort()
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_applications": n,
        "n_responses": n_responded,
        "response_rate": round(n_responded / n, 3) if n else 0.0,
        "n_interviews": sum(by_status.get(s, 0) for s in INTERVIEWED),
        "median_response_days": delays[len(delays) // 2] if delays else None,
        "by_status": by_status,
        "funnel": {
            "envoyees": n,
            "reponses": n_responded,
            "entretiens": sum(by_status.get(s, 0) for s in INTERVIEWED),
            "acceptees": by_status.get("acceptee", 0),
        },
        "n_follow_ups_done": sum(1 for a in apps if parse_date(a.get("date_relance"))),
        "weekly_target": prefs.get("weekly_target", 5),
        "by_week": dict(sorted(by_week.items())),
        "n_linked_to_pipeline": n_linked,
        "by_score_band": by_band,
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
    for company, role, days, kind in due:
        print(f"   - {company} ({role}) : {days} jours sans nouvelles -> {kind}")
    if due:
        alerts.append(f"{len(due)} relance(s) a faire")

    print(f"\n2. Fraicheur des donnees (alerte au-dela de {MAX_DATA_AGE_DAYS} jours) :")
    for source, age in ages.items():
        label = "absente" if age is None else f"{age} jour(s)"
        print(f"   - {source} : {label}")
        if age is None or age > MAX_DATA_AGE_DAYS:
            alerts.append(f"collecte {source} a relancer")

    print(f"\n3. Offres du top {TOP_N} de mon classement auxquelles je n'ai pas postule : {len(high)}")
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
        "high_match_rule": f"top {TOP_N} multicritere" if load_json(RANKING) else f"competences >= {HIGH_SCORE}",
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
