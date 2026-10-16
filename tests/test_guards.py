from app.planner import (Item, Plan, Policy, check_policy, deterministic_compose, empty_lessons, enforce_policy,
                         is_alcoholic, validate)

POOLS = {
    "artists": [{"id": "a1", "name": "Real Artist", "affinity": 0.9}, {"id": "a2", "name": "Second", "affinity": 0.8}],
    "brands": [{"id": "b1", "name": "Brand One", "affinity": 0.7}],
    "eats": [{"id": "e1", "name": "Noodle Place", "affinity": 0.8, "lat": 1.0, "lon": 1.0,
              "tags": [{"id": "urn:tag:genre:place:restaurant:ramen", "name": "Ramen"}]}],
    "bars": [{"id": f"r{i}", "name": f"Bar {i}", "affinity": 0.6, "lat": 1.0, "lon": 1.0} for i in range(6)],
    "tags": [{"id": "t1", "name": "matcha", "affinity": 0.9}, {"id": "t2", "name": "tacos", "affinity": 0.8}],
    "demographics": {"age": {"24_and_younger": 0.4}, "gender": {}},
    "heatmap": [{"lat": 1.01, "lon": 1.01, "affinity": 0.9}],
}
CTX = {"headliner": "Someone", "city": "Somewhere"}


def llm_style_plan(**over) -> Plan:
    base = dict(
        headline="h", audience_read="r",
        food=[Item(title="Ramen counter", evidence=["e1", "made-up"])],
        drinks=[Item(title="Gin spritz"), Item(title="Zero-proof matcha tonic")],
        non_alcoholic_share_pct=30,
        dj_set=[Item(title="Real Artist", evidence=["a1"]), Item(title="Invented Star", evidence=["fake-id"])],
        brands=[Item(title="Brand One", evidence=["b1"])],
        after_show=[Item(title=f"Bar {i}", evidence=[f"r{i}"]) for i in range(6)],
        promo_focus=["p"], run_of_show=["s"],
    )
    base.update(over)
    return Plan(**base)


def test_fake_id_in_dj_set_is_dropped():
    plan, dropped = validate(llm_style_plan(), POOLS)
    assert [i.title for i in plan.dj_set] == ["Real Artist"]
    assert any(d["title"] == "Invented Star" for d in dropped)
    # creative sections keep the item but lose the unknown id
    assert plan.food[0].evidence == ["e1"]


def test_wrong_name_with_valid_id_is_renamed_to_the_entity():
    plan, dropped = validate(llm_style_plan(dj_set=[Item(title="Somebody Else", evidence=["a2"])]), POOLS)
    assert plan.dj_set[0].title == "Second"
    assert "renamed" in dropped[0]["reason"]


def test_no_alcohol_means_100_percent_and_no_alcoholic_drinks():
    policy = Policy(no_alcohol=True)
    plan, _ = validate(llm_style_plan(), POOLS)
    assert check_policy(plan, policy, empty_lessons())
    fixed, fixes = enforce_policy(plan, policy, empty_lessons(), POOLS, CTX)
    assert fixed.non_alcoholic_share_pct == 100
    assert not any(is_alcoholic(d) for d in fixed.drinks)
    assert not check_policy(fixed, policy, empty_lessons())
    det = deterministic_compose(CTX, policy, POOLS, empty_lessons())
    assert det.non_alcoholic_share_pct == 100 and not any(is_alcoholic(d) for d in det.drinks)


def test_required_dietary_option_is_added():
    policy = Policy(dietary=["halal"])
    plan, _ = validate(llm_style_plan(), POOLS)
    assert "Food must include a halal option." in check_policy(plan, policy, empty_lessons())
    fixed, _ = enforce_policy(plan, policy, empty_lessons(), POOLS, CTX)
    assert any("halal" in f.title.lower() for f in fixed.food)
    assert not check_policy(fixed, policy, empty_lessons())


def test_partner_cap_is_enforced():
    policy = Policy(max_partners=3)
    plan, _ = validate(llm_style_plan(), POOLS)
    assert any("Too many after-show partners" in v for v in check_policy(plan, policy, empty_lessons()))
    fixed, _ = enforce_policy(plan, policy, empty_lessons(), POOLS, CTX)
    assert len(fixed.after_show) == 3
    assert len(deterministic_compose(CTX, policy, POOLS, empty_lessons()).after_show) == 3


def test_declined_partner_and_flop_are_removed():
    lessons = {**empty_lessons(), "declined": ["Bar 0"], "flopped": ["Ramen counter"]}
    plan, _ = validate(llm_style_plan(), POOLS)
    fixed, _ = enforce_policy(plan, Policy(max_partners=4), lessons, POOLS, CTX)
    assert "Bar 0" not in [p.title for p in fixed.after_show]
    assert len(fixed.after_show) == 4
    assert "Ramen counter" not in [f.title for f in fixed.food]
    assert fixed.food  # the flop gets replaced


def test_na_markers_are_not_alcoholic():
    assert not is_alcoholic(Item(title="Zero-proof spritz"))
    assert not is_alcoholic(Item(title="Non-alcoholic IPA"))
    assert is_alcoholic(Item(title="Mezcal feature pour"))
    assert not is_alcoholic(Item(title="Yuzu lemonade"))


def test_young_audience_bumps_na_share_within_bounds():
    plan = deterministic_compose(CTX, Policy(), POOLS, empty_lessons())
    assert 25 < plan.non_alcoholic_share_pct <= 45
    assert any(i.title.lower().startswith("zero-proof") for i in plan.drinks)
