"""The emulator harness itself: writes land in the emulator and clearing removes them."""

from google.cloud import firestore

from tests.conftest import TEST_PROJECT, clear_emulator


async def test_emulator_round_trip_and_clear(firestore_emulator):
    clear_emulator(firestore_emulator)
    client = firestore.AsyncClient(project=TEST_PROJECT)
    ref = client.collection("harness").document("probe")
    await ref.set({"hello": "world"})
    assert (await ref.get()).to_dict() == {"hello": "world"}

    clear_emulator(firestore_emulator)
    assert not (await ref.get()).exists
    client.close()
    await client._firestore_api_internal.transport.close()
