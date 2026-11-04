"""People <-> entities is many-to-many, through Membership(entity, person, role).

The household used here:
    Phil (personal)           Phil   owner
    Philip & Debbie (joint)   Phil   owner,  Debbie owner
    Family Trust 2000         Phil   trustee, Kai beneficiary
"""
import pytest

from migrations import migrate
from models import Entity, Membership, MembershipRole, Person, Portfolio, ValidationError


def household():
    return Portfolio(
        people=[Person(person_id="phil", name="Phil", birth_year=1965, birth_month=2),
                Person(person_id="debbie", name="Debbie", birth_year=1964, birth_month=10),
                Person(person_id="kai", name="Kai", birth_year=1995)],
        entities=[Entity(entity_id="phil_personal", name="Phil", entity_type="personal"),
                  Entity(entity_id="joint", name="Philip & Debbie Neufeld", entity_type="joint"),
                  Entity(entity_id="trust", name="Neufeld Family Trust 2000", entity_type="trust")],
        memberships=[Membership(entity_id="phil_personal", person_id="phil"),
                     Membership(entity_id="joint", person_id="phil"),
                     Membership(entity_id="joint", person_id="debbie"),
                     Membership(entity_id="trust", person_id="phil", role="trustee"),
                     Membership(entity_id="trust", person_id="kai", role="beneficiary")],
    )


def names(pairs):
    return sorted((x.name, role.value) for x, role in pairs)


# --- many-to-many, both directions ---------------------------------------------------------

def test_one_entity_many_people():
    assert names(household().people_in("joint")) == [("Debbie", "owner"), ("Phil", "owner")]


def test_one_person_many_entities():
    assert names(household().entities_of("phil")) == [
        ("Neufeld Family Trust 2000", "trustee"), ("Phil", "owner"), ("Philip & Debbie Neufeld", "owner")]


def test_roles_are_part_of_the_link():
    assert names(household().people_in("trust")) == [("Kai", "beneficiary"), ("Phil", "trustee")]


def test_same_person_can_hold_two_roles_in_one_entity():
    p = household()
    p.add_membership(Membership(entity_id="trust", person_id="phil", role="beneficiary"))
    assert names(p.entities_of("phil")).count(("Neufeld Family Trust 2000", "trustee")) == 1
    assert ("Neufeld Family Trust 2000", "beneficiary") in names(p.entities_of("phil"))


# --- integrity --------------------------------------------------------------------------------

def test_exact_duplicate_link_is_rejected():
    with pytest.raises(ValidationError):
        household().add_membership(Membership(entity_id="joint", person_id="debbie"))


def test_link_to_unknown_person_or_entity_is_rejected():
    p = household()
    with pytest.raises(ValidationError):
        p.add_membership(Membership(entity_id="joint", person_id="nobody"))
    with pytest.raises(ValidationError):
        p.add_membership(Membership(entity_id="nowhere", person_id="phil"))


def test_bad_role_is_rejected():
    with pytest.raises(ValidationError):
        Membership(entity_id="joint", person_id="phil", role="emperor")


def test_removing_a_person_removes_only_their_links():
    p = household()
    p.remove_person("debbie")
    assert names(p.people_in("joint")) == [("Phil", "owner")]
    assert p.get_entity("joint") is not None


def test_removing_an_entity_removes_its_links_but_keeps_the_people():
    p = household()
    p.remove_entity("trust")
    assert p.get_person("kai") is not None
    assert p.entities_of("kai") == []


def test_removing_one_link():
    p = household()
    p.remove_membership("trust|kai|beneficiary")
    assert names(p.people_in("trust")) == [("Phil", "trustee")]


# --- saving, loading, upgrading ------------------------------------------------------------------

def test_memberships_survive_save_and_load():
    p = household()
    assert Portfolio.from_dict(p.to_dict()).to_dict() == p.to_dict()


def test_interim_entity_person_link_becomes_an_owner_membership():
    data = Portfolio(people=[Person(person_id="phil", name="Phil", birth_year=1965)],
                     entities=[Entity(entity_id="e", name="Phil")]).to_dict()
    data["metadata"]["file_version"] = "0.5"
    data.pop("memberships")
    data["entities"][0]["person_id"] = "phil"  # how the brief v0.5 build stored it
    p = Portfolio.from_dict(data)
    assert names(p.people_in("e")) == [("Phil", "owner")]


def test_old_files_get_an_empty_membership_list(v0_1_data):
    assert migrate(v0_1_data)["memberships"] == []
