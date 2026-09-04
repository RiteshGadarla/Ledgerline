"""The corpus a new account starts with.

An account with nothing in it is a fair description of the product's state, and
a poor description of what it does. Registering used to land you on an empty
console with a Generate button: three screens of setup before anything the
engine does is visible. So an account is created holding one ready dataset,
seeded and reproducible, waiting to be run.

It is generated, never uploaded, and named as a demo, because a corpus that
arrives with its own answer key is the only kind precision and recall can be
measured against -- and measuring them is the point of showing it at all.
"""

import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from datagen.generator import generate_corpus
from datagen.serialize import truth_to_dict
from db.tenancy import (
    DatasetRecord,
    DatasetRole,
    create_dataset,
    dataset_name_taken,
    recompute_dataset_status,
    upsert_dataset_file,
)
from ingest.dataset_records import records_to_json

DEMO_DATASET_NAME = "Demo corpus"
# Fixed rather than random: every new account gets the same books, so a figure
# quoted from one demo is a figure anyone else can reproduce.
DEMO_SEED = 1001
DEMO_SIZE = 400


async def build_generated_dataset(
    db: AsyncSession, user_id: str, name: str, seed: int, size: int
) -> DatasetRecord:
    """Generate a corpus, persist its four roles and its answer key.

    The single place a generated dataset is built. The datasets endpoint and
    new-account seeding both come through here, so the demo cannot drift into
    being a different shape of dataset from the ones a user makes.
    """
    corpus, truth = generate_corpus(seed, size)
    dataset = await create_dataset(
        db, user_id, name, "generated", seed=seed, size=size, truth_json=json.dumps(truth_to_dict(truth))
    )
    role_records: dict[DatasetRole, list[Any]] = {
        "ledger": corpus.invoices,
        "gateway": corpus.payments,
        "settlement": corpus.settlements,
        "bank": corpus.bank_lines,
    }
    for role, records in role_records.items():
        await upsert_dataset_file(
            db,
            dataset.id,
            role,
            raw_filename=None,
            raw_content_type=None,
            raw_content=None,
            records_json=records_to_json(role, records),
            row_count=len(records),
            valid_count=len(records),
        )
    await recompute_dataset_status(db, dataset.id)
    return dataset


async def seed_demo_dataset(db: AsyncSession, user_id: str) -> DatasetRecord | None:
    """Give a new account something to run. Returns None if it already has a
    dataset by this name, so seeding is safe to call more than once.

    Deliberately not a run: the pipeline is worth watching, and a first run the
    user starts themselves shows the stages streaming rather than presenting a
    finished number they had no part in. It also keeps registration honest --
    signing up does not silently queue background work against an account.
    """
    if await dataset_name_taken(db, user_id, DEMO_DATASET_NAME):
        return None
    return await build_generated_dataset(db, user_id, DEMO_DATASET_NAME, DEMO_SEED, DEMO_SIZE)
