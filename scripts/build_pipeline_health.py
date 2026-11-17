import json, os, time

# Rassemble les indicateurs de sante du pipeline (collecte, fusion, extraction,
# matching) dans un seul fichier publie pour le dashboard.
# Aucune offre ni aucun texte d'offre : uniquement des comptages.


def load(path):
    if not os.path.exists(path):
        print(f"{path} absent -- ignore.")
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    adzuna = load("data/offers/raw_offers.json")
    jooble = load("data/offers/raw_offers_jooble.json")
    merged = load("data/offers/merged_offers.json")
    structured = load("data/offers/structured_offers.json")
    matches = load("docs/data/matches.json")

    sources = {}
    for name, raw in (("adzuna", adzuna), ("jooble", jooble)):
        stats = merged.get("sources", {}).get(name, {})
        sources[name] = {
            "fetched_at": raw.get("fetched_at"),
            "api_calls_last_run": raw.get("api_calls_used"),
            "per_query": raw.get("per_query", {}),
            "n_raw": raw.get("n_raw"),
            "n_duplicates_removed_in_source": raw.get("n_duplicates_removed"),
            "n_offers": stats.get("n_offers"),
            "n_internship": stats.get("n_internship"),
        }

    offers = structured.get("offers", [])
    matched_offers = matches.get("offers", [])
    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": sources,
        "merge": {
            "n_cross_source_duplicates": merged.get("n_cross_source_duplicates"),
            "n_same_source_duplicates": merged.get("n_same_source_duplicates"),
            "n_unique_offers": merged.get("n_unique_offers"),
            "n_out_of_scope": merged.get("n_out_of_scope"),
            "n_internships": len(merged.get("offers", [])),
        },
        "extraction": {
            "model": structured.get("model"),
            "n_offers": len(offers),
            "n_errors": structured.get("n_extraction_errors"),
            "avg_seconds_per_offer": structured.get("avg_seconds_per_offer"),
            "n_with_at_least_one_skill": sum(1 for o in offers if o.get("required_skills")),
        },
        "matching": {
            "n_offers": matches.get("n_offers"),
            "n_insufficient_data": matches.get("n_insufficient_data"),
            "share_matches_found_only_by_text": matches.get("share_matches_found_only_by_text"),
            "n_score_at_least_0_3": sum(1 for o in matched_offers if o.get("score", 0) >= 0.3),
        },
    }
    os.makedirs("docs/data", exist_ok=True)
    with open("docs/data/pipeline_health.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print("Ecrit : docs/data/pipeline_health.json")
    print(json.dumps({k: v for k, v in output.items() if k != "sources"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
