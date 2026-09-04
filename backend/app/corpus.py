"""Building a generated corpus and persisting it as a dataset.

A new account starts empty, on purpose: a corpus the user did not choose is
one they cannot account for, and the number it produces is not theirs. The
console walks them through generating one instead, which is also the moment
the seed, the size and the answer key are worth explaining.

Everything generated comes through here -- the datasets endpoint is the only
caller -- so a corpus made from the console is the same shape of dataset as
one that arrives as four uploaded files.
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
    recompute_dataset_status,
    upsert_dataset_file,
)
from ingest.dataset_records import records_to_json


async def build_generated_dataset(
    db: AsyncSession, user_id: str, name: str, seed: int, size: int
) -> DatasetRecord:
    """Generate a corpus, persist its four roles and its answer key."""
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
