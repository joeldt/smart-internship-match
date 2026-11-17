import html, json, os, re, unicodedata

ADZUNA_PATH = "data/offers/raw_offers.json"
JOOBLE_PATH = "data/offers/raw_offers_jooble.json"
MERGED_PATH = "data/offers/merged_offers.json"

# Dans le TITRE : mots entiers ("intern" seul attrapait aussi "international").
INTERNSHIP_TITLE_RE = re.compile(r"\b(stages?|stagiaires?|internships?|interns?|pfe)\b")
# Dans la DESCRIPTION : seulement des expressions sans ambiguite, car en anglais
# "stage" veut dire "etape" ("lifecycle stage", "early-stage startup").
INTERNSHIP_DESC_RE = re.compile(r"\b(stagiaires?|internships?|stage de fin d.?[eé]tudes|convention de stage)\b")
APPRENTICE_RE = re.compile(r"\b(alternance|alternants?|apprentis?|apprentissage)\b")

# Sites qui republient les offres d'autres entreprises : leur nom n'identifie pas l'employeur.
AGGREGATORS = {"direct emploi", "forums talents handicap", "jobteaser", "hellowork",
               "meteojob", "jobijoba", "indeed"}

def load_json(path):
    if not os.path.exists(path):
        print(f"{path} absent -- source ignoree.")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def clean_text(text):
    # Retire les balises HTML (<b>...</b>), decode &nbsp; etc., compacte les espaces.
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def norm(text):
    # Version "comparable" d'un texte : sans accents, minuscules, sans H/F, sans ponctuation.
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"\b[hfm]\s*/\s*[hfm]\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()

def clean_url(url):
    # Retire les parametres de suivi du lien Adzuna : ils contiennent l'App ID.
    return url.split("?")[0] if url else None

def flags(title, description):
    title, description = title.lower(), description.lower()
    return {
        "is_internship": bool(INTERNSHIP_TITLE_RE.search(title) or INTERNSHIP_DESC_RE.search(description)),
        "is_apprenticeship": bool(APPRENTICE_RE.search(f"{title} {description}")),
    }

def normalize_adzuna(raw_offers):
    out = []
    for o in raw_offers:
        title = clean_text(o.get("title"))
        description = clean_text(o.get("description"))
        salary = o.get("salary_min")
        predicted = str(o.get("salary_is_predicted")) == "1"
        out.append({
            "id": f"adz_{o['id']}",
            "source": "adzuna",
            "title": title,
            "company": (o.get("company") or {}).get("display_name"),
            "location": (o.get("location") or {}).get("display_name"),
            "description": description,
            "url": clean_url(o.get("redirect_url")),
            "date_posted": o.get("created"),
            "matched_queries": o.get("matched_queries", []),
            **flags(title, description),
            # Un salaire estime par Adzuna n'est pas un salaire annonce : on l'ignore.
            "salary_text": f"{salary} EUR/an" if salary and not predicted else None,
            "publishable": True,  # conditions Adzuna : publication autorisee avec "Jobs by Adzuna"
        })
    return out

def normalize_jooble(raw_offers):
    out = []
    for o in raw_offers:
        title = clean_text(o.get("title"))
        description = clean_text(o.get("snippet"))
        out.append({
            "id": f"jbl_{o.get('id')}",
            "source": "jooble",
            "title": title,
            "company": o.get("company") or None,
            "location": o.get("location") or None,
            "description": description,
            "url": o.get("link"),
            "date_posted": (o.get("updated") or "")[:19] or None,
            "matched_queries": o.get("matched_queries", []),
            **flags(title, description),
            "salary_text": clean_text(o.get("salary")) or None,
            "publishable": True,  # l'API Jooble est faite pour publier les resultats sur son site
        })
    return out

def dedupe_key(o):
    # "Stage : Stage - Developpeur Java" = "Stage - Developpeur Java" : on retire le mot "stage".
    title = re.sub(r"\b(stages?|pfe)\b", " ", norm(o["title"]))
    title = re.sub(r"\s+", " ", title).strip()
    company = norm(o.get("company"))
    return title, ("" if company in AGGREGATORS else company)

def dedupe(offers):
    # Meme titre + meme entreprise = meme offre. Si l'une des deux "entreprises" est
    # un agregateur (ou inconnue), le titre seul suffit. On garde la premiere offre
    # et on lui ajoute l'extrait de l'autre (Adzuna : debut, Jooble : mots-cles).
    kept, by_title = {}, {}
    n_cross, n_same = 0, 0
    for o in offers:
        title, company = dedupe_key(o)
        first = kept.get((title, company))
        if first is None and title in by_title:
            other_company, other = by_title[title]
            if company == "" or other_company == "":
                first = other
        if first is None:
            o["also_on"] = []
            kept[(title, company)] = o
            by_title.setdefault(title, (company, o))
            continue

        if o["source"] != first["source"]:
            n_cross += 1
            if o["source"] not in first["also_on"]:
                first["also_on"].append(o["source"])
        else:
            n_same += 1
        # On remplace le nom d'un agregateur par celui du vrai employeur.
        if norm(first.get("company")) in AGGREGATORS and company:
            first["company"] = o["company"]
        if o["description"] and o["description"] not in first["description"]:
            first["description"] = f"{first['description']} [...] {o['description']}"
        for q in o["matched_queries"]:
            if q not in first["matched_queries"]:
                first["matched_queries"].append(q)
    return list(kept.values()), n_cross, n_same

def source_stats(offers, raw):
    return {
        "fetched_at": (raw or {}).get("fetched_at"),
        "api_calls_used": (raw or {}).get("api_calls_used"),
        "n_offers": len(offers),
        "n_internship": sum(1 for o in offers if o["is_internship"]),
        "n_apprenticeship": sum(1 for o in offers if o["is_apprenticeship"]),
    }

def main():
    adzuna_raw = load_json(ADZUNA_PATH)
    jooble_raw = load_json(JOOBLE_PATH)
    adzuna = normalize_adzuna(adzuna_raw["offers"]) if adzuna_raw else []
    jooble = normalize_jooble(jooble_raw["offers"]) if jooble_raw else []
    stats = {"adzuna": source_stats(adzuna, adzuna_raw), "jooble": source_stats(jooble, jooble_raw)}

    merged_all, n_cross, n_same = dedupe(adzuna + jooble)
    # Perimetre du projet : les stages. Les offres hors sujet sont comptees, pas analysees.
    in_scope = [o for o in merged_all if o["is_internship"]]

    for name, s in stats.items():
        if s["n_offers"]:
            print(f"  {name} : {s['n_internship']}/{s['n_offers']} offres de stage "
                  f"({100 * s['n_internship'] / s['n_offers']:.0f} % de pertinence)")
    print(f"Doublons retires : {n_cross} entre les deux sources, {n_same} republications dans une meme source")
    print(f"{len(merged_all)} offres uniques, dont {len(in_scope)} stages gardes pour l'analyse "
          f"({len(merged_all) - len(in_scope)} hors sujet ecartees)")

    with open(MERGED_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "sources": stats,
            "n_cross_source_duplicates": n_cross,
            "n_same_source_duplicates": n_same,
            "n_unique_offers": len(merged_all),
            "n_out_of_scope": len(merged_all) - len(in_scope),
            "offers": in_scope,
        }, f, indent=2, ensure_ascii=False)
    print(f"Ecrit : {MERGED_PATH} (local uniquement, ignore par Git)")

if __name__ == "__main__":
    main()