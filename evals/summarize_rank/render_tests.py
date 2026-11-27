"""
render_tests.py -- dataset -> promptfoo test cases for summarize_rank eval.

CLI:
  render_tests.py --dataset <file> --out <file>

Reads a JSONL dataset and writes promptfoo test cases as YAML.
No labels file is required — this is a regression/stability eval, not an
accuracy eval against human ground truth.
"""

import argparse
import json
from pathlib import Path

import yaml


def load_dataset(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def build_test_cases(dataset: list[dict]) -> list[dict]:
    test_cases = []
    for record in dataset:
        article_id = record["article_id"]
        test_case = {
            "description": f"article-{article_id}",
            "vars": {
                "article_id": article_id,
                "source_name": record.get("source_name"),
                "clean_title": record.get("clean_title"),
                "feed_title": record.get("feed_title"),
                "title": record.get("title"),
                "content_text": record.get("content_text"),
                "excerpt": record.get("excerpt"),
                "feed_summary": record.get("feed_summary"),
                "production_score": record.get("production_score"),
                "interest_profile_text": record.get("interest_profile_text", ""),
                "categories": record.get("categories", []),
            },
            "metadata": {
                "article_id": article_id,
                "production_score": record.get("production_score"),
            },
        }
        test_cases.append(test_case)
    return test_cases


def _null_safe_representer(dumper: yaml.Dumper, _value: None) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "null")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Convert dataset into promptfoo test cases for summarize_rank eval."
    )
    parser.add_argument("--dataset", required=True, help="JSONL dataset file")
    parser.add_argument("--out", required=True, help="Output file (.yaml/.yml or .json)")
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    test_cases = build_test_cases(dataset)

    out_path = args.out
    suffix = Path(out_path).suffix.lower()

    if suffix in (".yaml", ".yml"):
        dumper = yaml.Dumper
        dumper.add_representer(type(None), _null_safe_representer)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(test_cases, f, Dumper=dumper, allow_unicode=True, sort_keys=False)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(test_cases, f, ensure_ascii=False, indent=2)
            f.write("\n")

    print(f"wrote {len(test_cases)} test cases to {out_path}")


if __name__ == "__main__":
    main()
