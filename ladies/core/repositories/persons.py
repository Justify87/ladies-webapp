from __future__ import annotations


class PersonRepository:
    def __init__(self, store):
        self._store = store

    def listing_target(self, advert_id):
        return self._store.listing_target(advert_id)

    def ingest_profile(self, **kwargs):
        return self._store.ingest_profile(**kwargs)

    def update_listing_status(self, *args, **kwargs):
        return self._store.update_listing_status(*args, **kwargs)

    def listing_detail(self, advert_id):
        return self._store.listing_detail(advert_id)

    def person_detail(self, person_id):
        return self._store.person_detail(person_id)

    def set_user_state(self, person_id, payload):
        return self._store.set_user_state(person_id, payload)

    def add_price_list(self, person_id, payload):
        return self._store.add_price_list(person_id, payload)

    def add_encounter(self, person_id, payload):
        return self._store.add_encounter(person_id, payload)

    def update_encounter(self, person_id, encounter_id, payload):
        return self._store.update_encounter(person_id, encounter_id, payload)

    def lists(self, tab):
        return self._store.lists(tab)

    def status_targets(self):
        return self._store.status_targets()

    def merge_persons(self, source_person_id, target_person_id):
        return self._store.merge_persons(source_person_id, target_person_id)

    def split_listing(self, listing_id):
        return self._store.split_listing(listing_id)
