import csv, json, math, os, re, statistics, time, unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Decision multicritere : a quelles offres postuler en priorite ?
#  1. Exclusion : on ecarte d'abord les offres qui ne peuvent pas convenir
#     (pas un stage, alternance, offre trop ancienne).
#  2. Evaluation : chaque offre restante recoit une note de 0 a 1 par critere,
#     puis une note globale = somme ponderee (poids dans preferences.json).
# Le score de competences (match_offers.py) n'est plus qu'UN critere parmi d'autres.
# ---------------------------------------------------------------------------

PREFS_PATH = "data/profile/preferences.json"
RATINGS_PATH = "data/profile/company_ratings.csv"
MATCHES_PATH = "data/offers/matches_all_local.json"
MERGED_PATH = "data/offers/merged_offers.json"

AGGREGATORS = {"direct emploi", "forums talents handicap", "jobteaser", "hellowork",
               "meteojob", "jobijoba", "indeed", "stage"}
REMOTE_RE = re.compile(r"t[eé]l[eé]-?travail|remote|hybride", re.I)
AI_RE = re.compile(r"\b(ia|ai|llm|genai)\b|intelligence artificielle|machine learning|generative|agentique", re.I)
QUERY_DOMAIN = {"stage développeur": "développement", "stage data": "data", "stage devops": "DevOps"}
SKILL_SCORE_MAX = 0.5   # meilleur score de competences atteignable en pratique (~0,47 observe)


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def company_key(name):
    # "THALES SA", "Thales" et "THALES" -> "thales" ; "Sopra-Steria" -> "sopra steria"
    key = norm(name)
    key = re.sub(r"\b(sa|sas|se|france|group|groupe)\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def linear(value, full, zero):
    """1 jusqu'a `full`, 0 a partir de `zero`, lineaire entre les deux."""
    if value <= full:
        return 1.0
    if value >= zero:
        return 0.0
    return round(1 - (value - full) / (zero - full), 3)


def distance_km(lat1, lon1, lat2, lon2):
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def location_criterion(offer, prefs):
    lat, lon = offer.get("latitude"), offer.get("longitude")
    if lat is not None and lon is not None:
        city, d = min(((c, distance_km(lat, lon, *xy)) for c, xy in prefs["cities"].items()), key=lambda x: x[1])
        return linear(d, prefs["distance_full_score_km"], prefs["distance_zero_score_km"]), round(d), city
    text = norm(offer.get("location"))
    for c in prefs["cities"]:
        if norm(c) in text:
            return 1.0, 0, c
    if "ile de france" in text:
        return 1.0, None, "Paris"
    return 0.5, None, None   # lieu inconnu ("France") : note neutre


def monthly_salary(text):
    if not text:
        return None
    numbers = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", text.replace(" ", ""))]
    if not numbers:
        return None
    amount = numbers[0]
    # Adzuna ecrit "EUR/an" meme pour une gratification mensuelle (650, 800...) :
    # un montant inferieur a 3 000 est forcement mensuel.
    return round(amount if amount < 3000 else amount / 12)


def load_ratings():
    ratings = {}
    if os.path.exists(RATINGS_PATH):
        with open(RATINGS_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ratings[company_key(row["company"])] = int(row["rating"])
    return ratings


def reputation_criterion(company, ratings, counts):
    key = company_key(company)
    if not key or key in AGGREGATORS:
        return 0.3, "inconnue"                      # employeur reel inconnu
    if key in ratings:
        return {3: 1.0, 2: 0.7, 1: 0.2}.get(ratings[key], 0.5), "ma liste"
    # Indicateur automatique : une entreprise qui publie plusieurs stages a un vrai
    # programme de stagiaires. Plafonne a 0,6 pour rester sous une entreprise de ma liste.
    return round(min(0.6, 0.3 + 0.1 * (counts.get(key, 1) - 1)), 2), "automatique"


def main():
    with open(PREFS_PATH, encoding="utf-8") as f:
        prefs = json.load(f)
    weights = prefs["weights"]
    ratings = load_ratings()
    with open(MATCHES_PATH, encoding="utf-8") as f:
        matches = json.load(f)
    with open(MERGED_PATH, encoding="utf-8") as f:
        merged = {o["id"]: o for o in json.load(f)["offers"]}

    now = datetime.now(timezone.utc)
    offers = [o for o in matches["offers"] if o["id"] in merged]
    counts = {}
    for o in offers:
        k = company_key(o.get("company"))
        counts[k] = counts.get(k, 0) + 1
    known_skill = [min(1, o["score"] / SKILL_SCORE_MAX) for o in offers if not o["insufficient_data"]]
    # Offre sans competence detectee : moitie de la mediane des offres connues.
    # On ne la suppose pas mauvaise, mais l'incertitude a un cout (attitude prudente).
    skill_unknown_value = round(0.5 * statistics.median(known_skill), 3) if known_skill else 0.25

    kept, excluded = [], {"alternance": 0, "trop ancienne": 0}
    for o in offers:
        m = merged[o["id"]]
        posted = m.get("date_posted")
        age = None
        if posted:
            date = datetime.fromisoformat(posted.replace("Z", "+00:00"))
            if date.tzinfo is None:              # dates Jooble sans fuseau
                date = date.replace(tzinfo=timezone.utc)
            age = (now - date).days
        # 1. Exclusion
        if m.get("is_apprenticeship"):
            excluded["alternance"] += 1
            continue
        if age is not None and age > prefs["exclude_older_than_days"]:
            excluded["trop ancienne"] += 1
            continue

        # 2. Evaluation, critere par critere (0 a 1)
        text = f"{m['title']} {m.get('description', '')}"
        loc, dist, city = location_criterion(m, prefs)
        rep, rep_source = reputation_criterion(o.get("company"), ratings, counts)
        salary = monthly_salary(m.get("salary_text"))
        domains = sorted({QUERY_DOMAIN.get(q, q) for q in m.get("matched_queries", [])}
                         | ({"IA"} if AI_RE.search(m["title"]) else set()))
        criteria = {
            "competences": skill_unknown_value if o["insufficient_data"] else round(min(1, o["score"] / SKILL_SCORE_MAX), 3),
            "localisation": loc,
            "reputation": rep,
            "fraicheur": linear(age, prefs["fresh_full_score_days"], prefs["fresh_zero_score_days"]) if age is not None else 0.5,
            "domaine": max((prefs["domains"].get(d, 0.5) for d in domains), default=0.5),
            # Sans montant, on suppose la gratification minimale legale -> 0.
            "salaire": linear(-salary, -prefs["salary_full_score_monthly"], -prefs["minimum_monthly_gratification"]) if salary else 0.0,
            "teletravail": 1.0 if REMOTE_RE.search(text) else 0.0,
        }
        global_score = round(sum(weights[c] * v for c, v in criteria.items()), 3)
        kept.append({
            "id": o["id"], "source": o["source"], "title": o["title"], "company": o.get("company"),
            "url": o.get("url"), "location": m.get("location"), "distance_km": dist, "nearest_city": city,
            "age_days": age, "salary_monthly": salary, "domains": domains,
            "skill_score": o["score"], "skills_unknown": o["insufficient_data"],
            "matched": o["matched"], "gaps": o["gaps"], "reputation_source": rep_source,
            "criteria": criteria, "global_score": global_score, "publishable": o.get("publishable", False),
        })

    kept.sort(key=lambda r: r["global_score"], reverse=True)
    by_skill = sorted(kept, key=lambda r: r["skill_score"], reverse=True)
    top_skill_ids = {r["id"] for r in by_skill[:12]}
    common = sum(1 for r in kept[:12] if r["id"] in top_skill_ids)

    print(f"{len(offers)} offres evaluees : {len(kept)} gardees, exclues : {excluded}")
    print(f"Note de competences donnee aux offres sans donnees (moitie de la mediane) : {skill_unknown_value}")
    print(f"{common} des 12 premieres offres sont aussi dans le top 12 selon les seules competences")
    print("Top 12 multicritere :")
    for r in kept[:12]:
        c = r["criteria"]
        print(f"  {r['global_score']:.3f}  {r['title'][:50]} @ {r['company']}  "
              f"(comp {c['competences']}, lieu {c['localisation']} {r['nearest_city'] or ''} {r['distance_km'] or ''} km, "
              f"rep {c['reputation']} {r['reputation_source']}, fraich {c['fraicheur']}, dom {c['domaine']}, sal {c['salaire']})")

    def output(rows):
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "weights": weights, "cities": list(prefs["cities"]), "domains": prefs["domains"],
            "skill_unknown_value": skill_unknown_value, "n_evaluated": len(offers), "excluded": excluded,
            "n_with_remote": sum(1 for r in rows if r["criteria"]["teletravail"]),
            "n_with_salary": sum(1 for r in rows if r["salary_monthly"]),
            "n_top12_also_top12_by_skill": common, "offers": rows,
        }

    with open("data/offers/ranking_all_local.json", "w", encoding="utf-8") as f:
        json.dump(output(kept), f, indent=2, ensure_ascii=False)
    public = [r for r in kept if r["publishable"]]
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/ranking.json", "w", encoding="utf-8") as f:
        json.dump(output(public), f, indent=2, ensure_ascii=False)
    print(f"Ecrit : docs/data/ranking.json ({len(public)} offres publiables)")


if __name__ == "__main__":
    main()
