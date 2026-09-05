import json
import os
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

API_ENDPOINT = "https://kgsearch.googleapis.com/v1/entities:search"

# 10 businesses sampled from qserve-leads-places-api-new/public/noncheap_october_leads.csv.
# We already know the website from Places, so this is a blind benchmark of KG Search.
BUSINESSES = [
    {"name": "Barranco Restaurant & Bar", "context": "Giza Egypt", "expected_website": "https://barrancocairo.com/"},
    {"name": "The Tap West", "context": "Sheikh Zayed Giza Egypt", "expected_website": "https://www.thetap.co/"},
    {"name": "Miss Li Lee's", "context": "Arkan Plaza Sheikh Zayed Cairo Egypt", "expected_website": "https://www.ihg.com/crowneplaza/hotels/us/en/cairo/caisz/hoteldetail/dining"},
    {"name": "Hatchi Specialty Coffee", "context": "Sheikh Zayed Giza Egypt", "expected_website": "https://hatchicoffee.com/"},
    {"name": "Koffee Kulture", "context": "Sheikh Zayed Giza Egypt", "expected_website": "https://koffee-kulture.com/"},
    {"name": "Nazim Coffee", "context": "Sheikh Zayed Giza Egypt", "expected_website": "https://nazim.coffee/"},
    {"name": "Dancing Goat Coffee", "context": "Galleria 40 Sheikh Zayed Giza Egypt", "expected_website": "https://www.dancinggoat.coffee/"},
    {"name": "Double dose coffee", "context": "Sheikh Zayed Giza Egypt", "expected_website": "http://doubledosecoffee.com/"},
    {"name": "Bean N' Bun", "context": "Al Guezira Plaza Sheikh Zayed Giza Egypt", "expected_website": "https://www.beannbuneg.com/"},
    {"name": "CAF Cafe", "context": "Sheikh Zayed Giza Egypt", "expected_website": "https://www.cafcafeeg.com/"},
]


def domain(url):
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return None


def request_kg(api_key, query):
    params = {
        "query": query,
        "limit": 5,
        "languages": "en",
        "key": api_key,
    }
    url = API_ENDPOINT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "qserve-kg-benchmark/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body[:2000]}
        return exc.code, payload
    except Exception as exc:
        return 0, {"error": {"message": repr(exc)}}


def compact_candidates(payload):
    out = []
    for item in payload.get("itemListElement", [])[:5]:
        result = item.get("result", {}) or {}
        out.append(
            {
                "name": result.get("name"),
                "url": result.get("url"),
                "description": result.get("description"),
                "types": result.get("@type"),
                "id": result.get("@id"),
                "resultScore": item.get("resultScore"),
                "detailedDescriptionUrl": (result.get("detailedDescription") or {}).get("url"),
            }
        )
    return out


def main():
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: GOOGLE_API_KEY secret is not available to the workflow.")
        return 0

    results = []
    successful_http_calls = 0

    for idx, business in enumerate(BUSINESSES, 1):
        query = f"{business['name']} {business['context']}"
        status, payload = request_kg(api_key, query)
        if status == 200:
            successful_http_calls += 1

        candidates = compact_candidates(payload) if status == 200 else []
        expected_domain = domain(business["expected_website"])
        top = candidates[0] if candidates else {}
        top_domain = domain(top.get("url"))
        top_match = bool(expected_domain and top_domain and expected_domain == top_domain)
        any_match = any(domain(c.get("url")) == expected_domain for c in candidates if c.get("url"))

        row = {
            "index": idx,
            "business": business["name"],
            "query": query,
            "http_status": status,
            "expected_website": business["expected_website"],
            "expected_domain": expected_domain,
            "top_result_name": top.get("name"),
            "top_result_url": top.get("url"),
            "top_result_domain": top_domain,
            "top_result_score": top.get("resultScore"),
            "top_domain_match": top_match,
            "expected_domain_in_top5": any_match,
            "candidates": candidates,
        }
        if status != 200:
            row["api_error"] = payload.get("error", payload)
        results.append(row)

        print(f"[{idx:02d}] {business['name']}")
        print(f"     HTTP: {status}")
        print(f"     Expected: {business['expected_website']}")
        if candidates:
            print(f"     KG top: {top.get('name')} | {top.get('url')}")
            print(f"     Match: top={top_match} any_top5={any_match}")
        else:
            err = payload.get("error", payload)
            print(f"     KG result: none/error | {json.dumps(err, ensure_ascii=False)[:500]}")

    matched_top = sum(1 for r in results if r["top_domain_match"])
    matched_any = sum(1 for r in results if r["expected_domain_in_top5"])
    with_url = sum(1 for r in results if r["top_result_url"])

    summary = {
        "tested": len(results),
        "successful_http_calls": successful_http_calls,
        "top_result_had_url": with_url,
        "top_domain_matches": matched_top,
        "expected_domain_found_in_top5": matched_any,
        "results": results,
    }

    with open("kg_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== BENCHMARK SUMMARY ===")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    print("\nRESULT_JSON_START")
    print(json.dumps(summary, ensure_ascii=False))
    print("RESULT_JSON_END")

    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with open(github_summary, "a", encoding="utf-8") as f:
            f.write("# Google Knowledge Graph benchmark\n\n")
            f.write(f"- HTTP-successful lookups: **{successful_http_calls}/{len(results)}**\n")
            f.write(f"- Top result returned `url`: **{with_url}/{len(results)}**\n")
            f.write(f"- Exact expected domain as top result: **{matched_top}/{len(results)}**\n")
            f.write(f"- Expected domain anywhere in top 5: **{matched_any}/{len(results)}**\n\n")
            f.write("| Business | Expected domain | KG top URL | Match | HTTP |\n")
            f.write("|---|---|---|---:|---:|\n")
            for r in results:
                kg_url = r["top_result_url"] or "—"
                f.write(f"| {r['business']} | {r['expected_domain']} | {kg_url} | {'✅' if r['top_domain_match'] else '❌'} | {r['http_status']} |\n")

    # Keep the workflow green even if Google rejects the request; the error itself is part of the test.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
