import asyncio

import pytest

from app.orchestrator.session import DocumentSession, SessionStore

DOC_ID = "aaaaaaaa-0000-0000-0000-000000000001"
TASK_ID = f"{DOC_ID}_general"
S3_KEY = f"{DOC_ID}_test.docx"
TEMPLATE = "SDA"


# ---------------------------------------------------------------------------
# SessionStore.create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_returns_session_with_correct_fields():
    store = SessionStore()
    session = await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})

    assert isinstance(session, DocumentSession)
    assert session.doc_id == DOC_ID
    assert session.template_type == TEMPLATE
    assert session.s3_key == S3_KEY
    assert session.expected_task_ids == {TASK_ID}
    assert session.collected_results == {}
    assert session.started_at is not None
    assert not session.completion_event.is_set()


@pytest.mark.asyncio
async def test_create_increments_active_count():
    store = SessionStore()
    assert store.active_count == 0
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})
    assert store.active_count == 1


@pytest.mark.asyncio
async def test_create_multiple_sessions():
    store = SessionStore()
    doc_a = "aaaaaaaa-0000-0000-0000-000000000001"
    doc_b = "bbbbbbbb-0000-0000-0000-000000000002"
    await store.create(doc_a, TEMPLATE, S3_KEY, {f"{doc_a}_general"})
    await store.create(doc_b, TEMPLATE, S3_KEY, {f"{doc_b}_general"})
    assert store.active_count == 2


# ---------------------------------------------------------------------------
# SessionStore.get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_created_session():
    store = SessionStore()
    created = await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})
    retrieved = store.get(DOC_ID)
    assert retrieved is created


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_doc_id():
    store = SessionStore()
    assert store.get("non-existent-id") is None


# ---------------------------------------------------------------------------
# SessionStore.record_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_result_returns_false_when_results_still_pending():
    store = SessionStore()
    task_a = f"{DOC_ID}_agent-a"
    task_b = f"{DOC_ID}_agent-b"
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {task_a, task_b})

    all_done = await store.record_result(DOC_ID, task_a, {"score": 80})

    assert all_done is False


@pytest.mark.asyncio
async def test_record_result_returns_true_when_last_result_arrives():
    store = SessionStore()
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})

    all_done = await store.record_result(DOC_ID, TASK_ID, {"score": 90})

    assert all_done is True


@pytest.mark.asyncio
async def test_record_result_sets_completion_event_when_all_received():
    store = SessionStore()
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})

    await store.record_result(DOC_ID, TASK_ID, {"score": 90})

    session = store.get(DOC_ID)
    assert session is not None
    assert session.completion_event.is_set()


@pytest.mark.asyncio
async def test_record_result_does_not_set_event_when_partial():
    store = SessionStore()
    task_a = f"{DOC_ID}_agent-a"
    task_b = f"{DOC_ID}_agent-b"
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {task_a, task_b})

    await store.record_result(DOC_ID, task_a, {"score": 70})

    session = store.get(DOC_ID)
    assert session is not None
    assert not session.completion_event.is_set()


@pytest.mark.asyncio
async def test_record_result_stores_result_in_collected_results():
    store = SessionStore()
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})
    result_payload = {"score": 95, "findings": ["ok"]}

    await store.record_result(DOC_ID, TASK_ID, result_payload)

    session = store.get(DOC_ID)
    assert session is not None
    assert session.collected_results[TASK_ID] == result_payload


@pytest.mark.asyncio
async def test_record_result_returns_false_for_unknown_doc_id():
    store = SessionStore()
    all_done = await store.record_result("ghost-id", TASK_ID, {"score": 1})
    assert all_done is False


@pytest.mark.asyncio
async def test_record_result_rejects_unexpected_task_id():
    store = SessionStore()
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})
    unexpected = f"{DOC_ID}_other"

    all_done = await store.record_result(DOC_ID, unexpected, {"score": 99})

    assert all_done is False
    session = store.get(DOC_ID)
    assert session is not None
    assert unexpected not in session.collected_results


@pytest.mark.asyncio
async def test_record_result_partial_results_when_both_arrive():
    store = SessionStore()
    task_a = f"{DOC_ID}_agent-a"
    task_b = f"{DOC_ID}_agent-b"
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {task_a, task_b})

    await store.record_result(DOC_ID, task_a, {"score": 70})
    all_done = await store.record_result(DOC_ID, task_b, {"score": 80})

    assert all_done is True
    session = store.get(DOC_ID)
    assert session is not None
    assert session.completion_event.is_set()
    assert len(session.collected_results) == 2


# ---------------------------------------------------------------------------
# SessionStore.remove
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_deletes_session():
    store = SessionStore()
    await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})
    await store.remove(DOC_ID)

    assert store.get(DOC_ID) is None
    assert store.active_count == 0


@pytest.mark.asyncio
async def test_remove_unknown_doc_id_is_safe():
    store = SessionStore()
    await store.remove("does-not-exist")  # must not raise


@pytest.mark.asyncio
async def test_remove_only_removes_target_session():
    store = SessionStore()
    doc_a = "aaaaaaaa-0000-0000-0000-000000000001"
    doc_b = "bbbbbbbb-0000-0000-0000-000000000002"
    await store.create(doc_a, TEMPLATE, S3_KEY, {f"{doc_a}_general"})
    await store.create(doc_b, TEMPLATE, S3_KEY, {f"{doc_b}_general"})

    await store.remove(doc_a)

    assert store.get(doc_a) is None
    assert store.get(doc_b) is not None
    assert store.active_count == 1


# ---------------------------------------------------------------------------
# Completion event integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_event_can_be_awaited_after_all_results():
    store = SessionStore()
    session = await store.create(DOC_ID, TEMPLATE, S3_KEY, {TASK_ID})

    await store.record_result(DOC_ID, TASK_ID, {"ok": True})

    # completion_event.wait() should return immediately since event is set
    await asyncio.wait_for(session.completion_event.wait(), timeout=1.0)
