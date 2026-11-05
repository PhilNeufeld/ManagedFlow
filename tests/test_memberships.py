"""People <-> entities is many-to-many, through Membership(entity, person, role).

The household used here:
    Alex (personal)           Alex   owner
    Alex & Sam (joint)   Alex   owner,  Sam owner
    Family Trust 2000         Alex   trustee, Kai beneficiary
"""
import pytest

from migrations import migrate
from models import Entity, Membership, MembershipRole, Person, Portfolio, ValidationError


def household():
    return Portfolio(
        people=[Person(person_id="alex", name="Alex", birth_year=1965, birth_month=2),
                Person(person_id="sam", name="Sam", birth_year=1964, birth_month=10),
                Person(person_id="kai", name="Kai", birth_year=1995)],
        entities=[Entity(entity_id="alex_personal", name="Alex", entity_type="personal"),
                  Entity(entity_id="joint", name="Alex & Sam Rivera", entity_type="joint"),
                  Entity(entity_id="trust", name="Rivera Family Trust 2000", entity_type="trust")],
        memberships=[Membership(entity_id="alex_personal", person_id="alex"),
                     Membership(entity_id="joint", person_id="alex"),
                     Membership(entity_id="joint", person_id="sam"),
                     Membership(entity_id="trust", person_id="alex", role="trustee"),
                     Membership(entity_id="trust", person_id="kai", role="beneficiary")],
    )


def names(pairs):
    return sorted((x.name, role.value) for x, role in pairs)


# --- many-to-many, both directions ---------------------------------------------------------

def test_one_entity_many_people():
    assert names(household().people_in("joint")) == sorted([("Sam", "owner"), ("Alex", "owner")])


def test_one_person_many_entities():
    assert names(household().entities_of("alex")) == sorted([
        ("Rivera Family Trust 2000", "trustee"), ("Alex", "owner"), ("Alex & Sam Rivera", "owner")])


def test_roles_are_part_of_the_link():
    assert names(household().people_in("trust")) == sorted([("Kai", "beneficiary"), ("Alex", "trustee")])


def test_same_person_can_hold_two_roles_in_one_entity():
    p = household()
    p.add_membership(Membership(entity_id="trust", person_id="alex", role="beneficiary"))
    assert names(p.entities_of("alex")).count(("Rivera Family Trust 2000", "trustee")) == 1
    assert ("Rivera Family Trust 2000", "beneficiary") in names(p.entities_of("alex"))


# --- integrity --------------------------------------------------------------------------------

def test_exact_duplicate_link_is_rejected():
    with pytest.raises(ValidationError):
        household().add_membership(Membership(entity_id="joint", person_id="sam"))


def test_link_to_unknown_person_or_entity_is_rejected():
    p = household()
    with pytest.raises(ValidationError):
        p.add_membership(Membership(entity_id="joint", person_id="nobody"))
    with pytest.raises(ValidationError):
        p.add_membership(Membership(entity_id="nowhere", person_id="alex"))


def test_bad_role_is_rejected():
    with pytest.raises(ValidationError):
        Membership(entity_id="joint", person_id="alex", role="emperor")


def test_removing_a_person_removes_only_their_links():
    p = household()
    p.remove_person("sam")
    assert names(p.people_in("joint")) == sorted([("Alex", "owner")])
    assert p.get_entity("joint") is not None


def test_removing_an_entity_removes_its_links_but_keeps_the_people():
    p = household()
    p.remove_entity("trust")
    assert p.get_person("kai") is not None
    assert p.entities_of("kai") == []


def test_removing_one_link():
    p = household()
    p.remove_membership("trust|kai|beneficiary")
    assert names(p.people_in("trust")) == sorted([("Alex", "trustee")])


# --- saving, loading, upgrading ------------------------------------------------------------------

def test_memberships_survive_save_and_load():
    p = household()
    assert Portfolio.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_interim_entity_person_link_becomes_an_owner_membership():
    data = Portfolio(people=[Person(person_id="alex", name="Alex", birth_year=1965)],
                     entities=[Entity(entity_id="e", name="Alex")]).to_dict()
    data["metadata"]["file_version"] = "0.5"
    data.pop("memberships")
    data["entities"][0]["person_id"] = "alex"  # how the brief v0.5 build stored it
    p = Portfolio.from_dict(data)
    assert names(p.people_in(p.entities[0].entity_id)) == sorted([("Alex", "owner")])   # keys renumbered in v1.0


def test_old_files_get_an_empty_membership_list(v0_1_data):
    assert migrate(v0_1_data)["memberships"] == []


# --- a personal entity is one person ---------------------------------------------------------

def test_personal_entity_can_have_only_one_owner():
    with pytest.raises(ValidationError, match="only one owner"):
        household().add_membership(Membership(entity_id="alex_personal", person_id="sam"))


def test_personal_entity_can_still_have_other_roles():
    p = household()
    p.add_membership(Membership(entity_id="alex_personal", person_id="sam", role="beneficiary"))
    assert names(p.people_in("alex_personal")) == sorted([("Sam", "beneficiary"), ("Alex", "owner")])


def test_joint_entity_cant_become_personal_while_it_has_two_owners():
    p = household()
    with pytest.raises(ValidationError, match="only one owner"):
        p.replace_entity("joint", Entity(entity_id="joint", name="Alex & Sam Rivera", entity_type="personal"))


def test_file_with_two_owners_on_a_personal_entity_is_rejected():
    data = household().to_dict()
    data["memberships"].append({"entity_id": "alex_personal", "person_id": "sam", "role": "owner", "notes": ""})
    with pytest.raises(ValidationError, match="only one owner"):
        Portfolio.from_dict(data)


# --- primary keys on the link table (v1.1) -------------------------------------------------------------

def test_links_get_integer_primary_keys_and_the_pair_stays_unique():
    p = household()
    ids = [m.membership_id for m in p.memberships]
    assert all(isinstance(i, int) for i in ids) and len(set(ids)) == len(ids)
    new = Membership(entity_id="trust", person_id="alex", role="beneficiary")
    p.add_membership(new)
    assert new.membership_id == max(ids) + 1
    with pytest.raises(ValidationError, match="already has that role"):
        p.add_membership(Membership(entity_id="trust", person_id="alex", role="beneficiary"))


def test_changing_a_role_keeps_the_primary_key():
    p = household()
    m = p.memberships[0]
    p.replace_membership(m.membership_id, Membership(entity_id=m.entity_id, person_id=m.person_id, role="other"))
    assert p.get_membership(m.membership_id).role.value == "other"
    p.remove_membership(m.membership_id)
    assert p.get_membership(m.membership_id) is None


def test_old_files_give_links_a_key():
    data = household().to_dict()
    for row in data["memberships"]:
        row.pop("membership_id")
    data["metadata"]["file_version"] = "1.0"
    p = Portfolio.from_dict(data)
    assert sorted(m.membership_id for m in p.memberships) == list(range(1, len(p.memberships) + 1))
