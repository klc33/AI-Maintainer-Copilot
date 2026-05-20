# model_server/check_data_completeness.py
"""Check whether the local Terraform dataset covers all issues expected from the API."""
import os
import requests
import pandas as pd

OWNER = "hashicorp"
REPO = "terraform"
MIN_DATE = "2020-01-01"

# GitHub search query – matches exactly what your data script fetches:
# closed issues, no PRs, updated since MIN_DATE
QUERY = f"repo:{OWNER}/{REPO}+is:issue+is:closed+updated:>={MIN_DATE}"
SEARCH_URL = "https://api.github.com/search/issues"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

def get_expected_count() -> int:
    """Return the total number of closed issues from GitHub's search API."""
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {"q": QUERY, "per_page": 1}  # we only care about total_count
    resp = requests.get(SEARCH_URL, headers=headers, params=params)
    if resp.status_code != 200:
        print(f"Search API error: {resp.status_code} {resp.text}")
        return None
    data = resp.json()
    total = data["total_count"]
    print(f"GitHub reports {total} closed issues matching the criteria.")
    return total

def main():
    if not GITHUB_TOKEN:
        print("Please set GITHUB_TOKEN environment variable.")
        return

    # 1. Read local CSV
    csv_path = "datasets/terraform_issues.csv"
    if not os.path.exists(csv_path):
        print(f"Local file {csv_path} not found. Run the data fetch first.")
        return

    df = pd.read_csv(csv_path)
    local_count = len(df)
    print(f"Local CSV contains {local_count} issues.")

    # 2. Get expected count from GitHub
    expected = get_expected_count()
    if expected is None:
        return

    # 3. Compare
    diff = expected - local_count
    if abs(diff) <= 5:   # small tolerance for API latency
        print("✅ Dataset is complete (counts match within tolerance).")
    else:
        print(f"⚠️ Discrepancy: GitHub has {expected}, local has {local_count}. "
              f"Difference: {diff}. The data may be incomplete.")

    # 4. Check class distribution
    print("\nLocal label distribution:")
    print(df["label"].value_counts())
    print("\nExpected classes: bug, feature, docs, question")

if __name__ == "__main__":
    main()