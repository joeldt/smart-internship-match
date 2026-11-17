import csv, json, os, re, time, unicodedata

# ---------------------------------------------------------------------------
# KPI de matching hybride :
#  1. competences extraites par l'IA (Ollama), souvent vides car les extraits
#     d'offres sont tronques, et parfois differentes pour une meme offre ;
#  2. + detection deterministe des competences du profil dans le titre et
#     l'extrait de l'offre, par motifs (variantes d'ecriture regroupees).
# ---------------------------------------------------------------------------

# Variantes d'ecriture de chaque competence du profil (texte en minuscules, sans accents).
SKILL_PATTERNS = {
    "Python": [r"\bpython\b"],
    "Java": [r"\bjava\b(?!\s*script)"],
    "Kotlin": [r"\bkotlin\b"],
    "Android": [r"\bandroid\b"],
    "Firebase": [r"\bfirebase\b", r"\bfirestore\b"],
    "Angular": [r"\bangular"],
    "Power BI": [r"\bpower\s*bi\b"],
    "Git": [r"\bgit\b", r"\bgithub\b", r"\bgitlab\b"],
    # Choix assume : CI/CD rattache a GitHub Actions (mes pipelines sont de la CI/CD).
    "GitHub Actions": [r"github actions", r"\bci\s*[/-]?\s*cd\b", r"integration continue"],
    "Web scraping": [r"scraping", r"beautifulsoup"],
    "LLM": [r"\bllms?\b", r"ia generative", r"generative ai", r"\bgenai\b", r"\brag\b",
            r"ia agentique", r"agentic", r"agents? intelligents?"],
    "JavaScript": [r"javascript", r"\bjs\b"],
    "C#": [r"c#", r"\.net\b"],
    "Data visualisation": [r"data ?vi[sz]", r"visualisation de donnees", r"tableaux? de bord", r"dashboards?"],
    "SQL": [r"\bsql\b", r"postgres", r"mysql"],
}

# Termes vagues renvoyes par l'IA (observes dans les extractions) : pas de vraies competences.
GENERIC_TERMS = {
    "data", "ia", "ai", "informatique", "automatisation", "cloud", "framework", "documentation",
    "plateforme", "application", "applications web internes", "outils metier", "logiciel interne",
    "developpement de solutions", "traitement de donnees", "analyse de donnees",
    "intelligence artificielle", "innovation industrielle", "tableau de bord automatise",
}
# Intitules de poste pris pour des competences par l'IA ("Developpeur(se)", "Data Analyst"...).
JOB_TITLE_RE = re.compile(r"developpeu|ingenieur|integrateu|consultant|data analyst|data scientist|analyste")

FIRST_FORM = {}  # premiere ecriture rencontree pour chaque lacune (affichage coherent)


def norm(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def canon(skill):
    c = norm(skill)
    c = re.sub(r"\bci\s*[-\s]\s*cd\b", "ci/cd", c)
    return c


def patterns_for(skill):
    # Competence ajoutee au profil sans motif connu : on cherche son nom tel quel.
    default = r"(?<![a-z0-9])" + re.escape(norm(skill)) + r"(?![a-z0-9])"
    return SKILL_PATTERNS.get(skill, [default])


def load_profile(path="data/profile/my_skills.csv"):
    profile = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            profile[row["skill"]] = {"level": int(row["level"]), "evidence": row["evidence"]}
    return profile


def score_offer(profile, required_skills, text=""):
    text_n = norm(text)
    llm_n = [norm(s) for s in required_skills]

    matched = []
    only_by_text = []
    for skill in profile:
        pats = patterns_for(skill)
        in_llm = any(re.search(p, s) for p in pats for s in llm_n)
        in_text = any(re.search(p, text_n) for p in pats)
        if in_llm or in_text:
            matched.append(skill)  # toujours le nom exact du profil
            if not in_llm:
                only_by_text.append(skill)

    gaps = []
    seen = set()
    for s in required_skills:
        c = canon(s)
        if not c or c in GENERIC_TERMS or JOB_TITLE_RE.search(c) or c in seen:
            continue
        if any(re.search(p, c) for sk in matched for p in patterns_for(sk)):
            continue  # deja couverte par une competence du profil
        seen.add(c)
        gaps.append(FIRST_FORM.setdefault(c, s.strip()))

    n_detected = len(matched) + len(gaps)
    weighted = sum(profile[s]["level"] / 5 for s in matched)

    # "+ 1" : au moins une competence demandee reste invisible (extrait tronque).
    score = round(weighted / (n_detected + 1), 3)
    if n_detected:
        coverage = round(len(matched) / n_detected, 3)
    else:
        coverage = 0.0

    result = {}
    result["score"] = score
    result["coverage"] = coverage
    result["matched"] = matched
    result["gaps"] = gaps
    result["matched_only_by_text"] = only_by_text
    result["n_skills_detected"] = n_detected
    result["insufficient_data"] = n_detected == 0
    return result


def average(values):
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def build_output(results, profile):
    gap_counts = {}
    matched_counts = {}
    for r in results:
        for g in r["gaps"]:
            gap_counts[g] = gap_counts.get(g, 0) + 1
        for s in r["matched"]:
            matched_counts[s] = matched_counts.get(s, 0) + 1
    top_gaps = sorted(gap_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top_matched = sorted(matched_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    n_matches = sum(len(r["matched"]) for r in results)
    n_text_only = sum(len(r["matched_only_by_text"]) for r in results)

    # Score moyen par recherche : sur toutes les offres, puis seulement sur celles
    # ou des competences ont ete detectees (separe "offre inconnue" et "peu adaptee").
    by_query = {}
    by_query_known = {}
    for r in results:
        for q in r.get("matched_queries", []):
            by_query.setdefault(q, []).append(r["score"])
            if not r["insufficient_data"]:
                by_query_known.setdefault(q, []).append(r["score"])

    output = {}
    output["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    output["n_offers"] = len(results)
    output["n_insufficient_data"] = sum(1 for r in results if r["insufficient_data"])
    output["share_matches_found_only_by_text"] = round(n_text_only / n_matches, 3) if n_matches else 0.0
    output["avg_score_by_query"] = {q: average(v) for q, v in by_query.items()}
    output["avg_score_by_query_known"] = {q: average(v) for q, v in by_query_known.items()}
    output["offers"] = results
    output["top_skill_gaps"] = [{"skill": s, "count": c} for s, c in top_gaps]
    output["top_matched_skills"] = [
        {"skill": s, "count": c, "level": profile[s]["level"]} for s, c in top_matched
    ]
    output["n_offers_with_compensation"] = sum(
        1 for r in results if r.get("compensation_mentioned") or r.get("salary_text")
    )
    return output


def main():
    profile = load_profile()

    with open("data/offers/structured_offers.json", encoding="utf-8") as f:
        offers = json.load(f)["offers"]

    # L'extrait de chaque offre est dans merged_offers.json (structured ne le garde pas).
    with open("data/offers/merged_offers.json", encoding="utf-8") as f:
        merged = json.load(f)["offers"]
    descriptions = {o["id"]: o.get("description") or "" for o in merged}

    # Les offres fusionnees comme doublons ne sont plus dans merged_offers.json : on les ignore.
    offers = [o for o in offers if o["id"] in descriptions]

    results = []
    for offer in offers:
        text = (offer.get("title") or "") + " " + descriptions[offer["id"]]
        scores = score_offer(profile, offer["required_skills"], text)
        results.append({**offer, **scores})
    results.sort(key=lambda r: r["score"], reverse=True)

    print("Top 15 des offres :")
    for r in results[:15]:
        print(f"  {r['score']:.3f}  {(r['title'] or '')[:55]} @ {r.get('company')}  "
              f"(trouvees : {r['matched']}, manquantes : {r['gaps']})")

    out_all = build_output(results, profile)
    print(f"\n{out_all['n_offers']} offres, dont {out_all['n_insufficient_data']} sans aucune competence detectee")
    print(f"Part des correspondances trouvees UNIQUEMENT par la recherche dans le texte : "
          f"{out_all['share_matches_found_only_by_text']:.0%}")
    print(f"Score moyen par recherche (toutes offres) : {out_all['avg_score_by_query']}")
    print(f"Score moyen par recherche (offres avec donnees) : {out_all['avg_score_by_query_known']}")
    print(f"Competences les plus demandees que j'ai : {[s['skill'] for s in out_all['top_matched_skills']]}")
    print(f"Lacunes les plus frequentes : {[(g['skill'], g['count']) for g in out_all['top_skill_gaps']]}")

    os.makedirs("data/offers", exist_ok=True)
    with open("data/offers/matches_all_local.json", "w", encoding="utf-8") as f:
        json.dump(out_all, f, indent=2, ensure_ascii=False)
    print("\nEcrit : data/offers/matches_all_local.json (local uniquement)")

    public = [r for r in results if r.get("publishable", False)]
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/matches.json", "w", encoding="utf-8") as f:
        json.dump(build_output(public, profile), f, indent=2, ensure_ascii=False)
    print(f"Ecrit : docs/data/matches.json ({len(public)} offres publiables sur {len(results)})")


if __name__ == "__main__":
    main()
