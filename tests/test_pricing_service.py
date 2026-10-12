from pathlib import Path

from ladies_v9.models import Person, PersonUserState
from ladies_v9.messaging.models import ChatMessage, ChatThread
from ladies_v9.pricing.service import PriceIntelligenceService


def _seed(service):
    with service.database.session() as db:
        person=Person(display_name="Test")
        db.add(person);db.flush();db.add(PersonUserState(person_id=person.person_id))
        thread=ChatThread(person_id=person.person_id)
        db.add(thread);db.flush()
        db.add(ChatMessage(thread_id=thread.thread_id,direction="OUT",message_type="TEXT",text="Was kostet es?",status="SENT"))
        db.add(ChatMessage(thread_id=thread.thread_id,direction="IN",message_type="TEXT",text="30 min 100 €, 1h 160 €",status="RECEIVED"))
        db.add(ChatMessage(thread_id=thread.thread_id,direction="IN",message_type="TEXT",text="Küssen inklusive",status="RECEIVED"))
        return person.person_id,thread.thread_id


def test_end_to_end_proposal_accept_and_history(tmp_path: Path):
    service=PriceIntelligenceService(tmp_path/"test.db",tmp_path/"data")
    person_id,thread_id=_seed(service)
    signal=service.signal(thread_id,allow_ocr=False)
    assert signal["strong"]
    proposal=service.create_proposal(thread_id,trigger_type="MANUAL")
    assert len(proposal["proposal"]["offers"])==2
    accepted=service.accept(proposal["price_extraction_id"],{})
    assert accepted["status"]=="ACCEPTED"
    current=service.current(person_id)
    assert current["price_list_id"]==accepted["accepted_price_list_id"]
    assert len(current["proposal"]["offers"])==2
    history=service.history(person_id)
    assert len(history)==1
    assert history[0]["revision"]["source_extraction_id"]==proposal["price_extraction_id"]


def test_same_prices_do_not_create_duplicate_revision(tmp_path: Path):
    service=PriceIntelligenceService(tmp_path/"test.db",tmp_path/"data")
    person_id,thread_id=_seed(service)
    first=service.create_proposal(thread_id)
    service.accept(first["price_extraction_id"],{})
    second=service.create_proposal(thread_id)
    result=service.accept(second["price_extraction_id"],{})
    assert result["status"]=="ACCEPTED_NO_CHANGE"
    assert len(service.history(person_id))==1
