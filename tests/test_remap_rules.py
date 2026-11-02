"""规则跨实例 remap：概念/算子按名称+主体映射。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from remap_rule_refs import remap  # noqa: E402


def test_remap_rule_refs():
    source = {
        "concepts": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
        "operators": [{"id": 10, "name": "op", "subject": "A"}],
        "rules": [{"name": "r", "body": None, "head": [
            {"lhs": {"operator": "oms-op-10", "arguments": ["x"]}, "rhs": None}]}],
        "individuals": [{"name": "i", "concept": "A"}],
    }
    target = {
        "concepts": [{"id": 7, "name": "A"}, {"id": 8, "name": "B"}],
        "operators": [{"id": 20, "name": "op", "subject": "A"}],
        "rules": [], "individuals": [],
    }
    out = remap(source, target)
    assert out["rules"][0]["head"][0]["lhs"]["operator"] == "oms-op-20"
    assert out["individuals"][0]["concept"] == "A"
    assert not out["warnings"]
