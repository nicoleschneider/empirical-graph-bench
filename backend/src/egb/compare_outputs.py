import argparse
import ast
import json

from experiment import Evaluator


def partition(path: str) -> list[set[str]]:
    with open(path, "r") as f:
        return [{str(v) for v in community} for community in ast.literal_eval(f.read())]


def scores(path: str) -> dict[str, float]:
    with open(path, "r") as f:
        return {str(k): v for k, v in ast.literal_eval(f.read()).items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("kind", choices=["scores", "communities"])
    ap.add_argument("targeted")
    ap.add_argument("baseline")
    flags = ap.parse_args()
    evaluator = Evaluator()
    if flags.kind == "scores":
        print(
            json.dumps(
                {
                    "l1": evaluator.calculate_l1_distance(
                        scores(flags.targeted), scores(flags.baseline)
                    )
                }
            )
        )
    else:
        targeted, baseline = partition(flags.targeted), partition(flags.baseline)
        if set().union(*targeted) != set().union(*baseline):
            raise SystemExit("The two partitions cover different vertex sets.")
        print(
            json.dumps(
                {
                    "identical": {frozenset(c) for c in targeted}
                    == {frozenset(c) for c in baseline},
                    "nmi_between": evaluator.calculate_nmi(
                        evaluator.prepare_community_labels(targeted),
                        evaluator.prepare_community_labels(baseline),
                    ),
                }
            )
        )
