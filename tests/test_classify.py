import os

from fscan.classify.sensitivity import SensitivityRules
from fscan.classify.topics import TopicRules, TopicScorer

RULES = os.path.join(os.path.dirname(__file__), "..", "rules")


def test_sensitivity_levels():
    r = SensitivityRules.load(os.path.join(RULES, "sensitivity.yaml"))
    assert r.level({"AADHAAR": 1}) == "HIGH"
    assert r.level({"PAN": 12}) == "HIGH"
    assert r.level({"PAN": 3}) == "MEDIUM"
    assert r.level({"EMAIL": 60}) == "MEDIUM"
    assert r.level({"EMAIL": 4, "PHONE_IN": 2}) == "LOW"
    assert r.level({}) == "NONE"
    assert r.level({}, unreadable=True) == "UNKNOWN"
    assert r.level({"PASSWORD": 1}, unreadable=True) == "HIGH"  # findings beat 'unknown'


def test_topics_from_path_and_content():
    rules = TopicRules.load(os.path.join(RULES, "topics.yaml"))
    t = TopicScorer(rules, r"\\fs\HR\Payroll\salary_march_2025.xlsx")
    t.add_text("Employee payslip for March")
    topics, scores = t.result()
    assert topics[0] == "HR/Payroll"
    assert scores["HR/Payroll"] >= rules.threshold


def test_content_hits_are_capped_per_keyword():
    rules = TopicRules.load(os.path.join(RULES, "topics.yaml"))
    t = TopicScorer(rules, r"\\fs\misc\notes.txt")
    t.add_text("invoice " * 1000)
    _, scores = t.result()
    assert scores.get("Finance/Accounts", 0) == rules.max_hits * rules.content_weight


def test_unclassified_when_below_threshold():
    rules = TopicRules.load(os.path.join(RULES, "topics.yaml"))
    t = TopicScorer(rules, r"\\fs\misc\holiday_photos.txt")
    t.add_text("Beach trip pictures")
    assert t.result()[0] == ["unclassified"]


def test_short_keywords_need_whole_words():
    rules = TopicRules.load(os.path.join(RULES, "topics.yaml"))
    t = TopicScorer(rules, r"\\fs\x\a.txt")
    t.add_text("hrefs, pfizer, glossary, powerpoint")   # contain hr / pf / gl / po inside words
    assert t.result()[1] == {}
