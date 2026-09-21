from uctbook import catalogue as cat


def test_locations(fixture):
    locations = cat.parse_locations(fixture("r-new.html"))
    assert [e.id for e in locations] == [5154, 7465, 9445]
    assert locations[0].name == "Chancellor Oppenheimer Library"


def test_zones_and_categories_are_per_location(fixture):
    zones, categories, capacities = cat.parse_location_detail(fixture("r-new-5154.html"))
    assert [z.id for z in zones] == [7304, 7642, 8087]
    assert [c.id for c in categories] == [13236, 13265, 13879, 33215]
    assert [c.id for c in capacities] == [-1, 1, 2]


def test_seats_carry_their_resource(fixture):
    seats = cat.parse_resources(fixture("spaces-33215.html"), 5154, 33215)
    assert len(seats) == 91
    first = seats[0]
    assert (first.id, first.name, first.eid) == (165230, "KC01", 131384)


def test_a_room_is_its_own_resource(fixture):
    rooms = cat.parse_resources(fixture("spaces-13236.html"), 5154, 13236)
    assert len(rooms) == 8
    first = rooms[0]
    assert first.id == first.eid == 50959
    assert first.capacity == 6
    assert first.name.startswith("Room 1")


def test_hand_written_names_survive_a_refresh(fixture):
    catalogue = cat.Catalogue(seats=[cat.Seat(id=165230, name="my favourite")])
    cat.merge_seats(catalogue, cat.parse_resources(fixture("spaces-33215.html"), 5154, 33215))
    assert catalogue.label_for(165230) == "my favourite"
    assert catalogue.label_for(165231) == "KC02"
